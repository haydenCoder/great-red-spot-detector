/* ============================================================================
 * cspeed.c — C-accelerated numeric kernels for GRS Observatory (v7.0.0).
 *
 * WHY this file exists (measured, not argued): profiling `ap_stacker` showed
 * ~91% of stack time inside `_lk_refine`, and inside that ~95% was five
 * `scipy.ndimage.map_coordinates(order=3, mode="nearest", prefilter=False)`
 * calls per iteration plus half a dozen numpy temporaries — pure per-call
 * overhead, not physics.  Those five samples (value at (y,x) and at
 * (y±1,x), (y,x±1) for central-difference gradients) share the SAME spline
 * weights because y±1 / x±1 keep the same fractional part: one fused pass
 * over a 6×6 coefficient neighbourhood yields value AND both gradients.
 *
 * PARITY CONTRACT: kernels replicate scipy's math to last-ULP scale;
 * tests/test_cspeed.py asserts max|delta| < 1e-12 against scipy on random
 * fields (measured ~1e-15, i.e. summation-order noise only).  If a machine
 * has no C compiler, app/cspeed.py falls back to the exact scipy path —
 * identical results, just slower (soft-fail loudly: one warning line).
 *
 * Numerics: strict IEEE double, no -ffast-math, no reassociation tricks.
 * We need accuracy invariance first, speed second.
 * ========================================================================= */
#include <math.h>
#include <string.h>

#define CS_VERSION_MAJOR 7
#define CS_VERSION_MINOR 0

int cs_version(void) { return CS_VERSION_MAJOR * 100 + CS_VERSION_MINOR; }

/* Uniform cubic B-spline basis (order 3), t in [0,1).
 * Taps pair with samples C[j-1], C[j], C[j+1], C[j+2] where j=floor(x).
 * Sanity: at t=0 the weights are (1/6, 4/6, 1/6, 0) — the exact uniform
 * B-spline node stencil, value = (C[j-1] + 4*C[j] + C[j+1]) / 6. */
static inline void w3(double t, double W[4]) {
    const double t2 = t * t, t3 = t2 * t;
    W[0] = (1.0 - t) * (1.0 - t) * (1.0 - t) / 6.0;
    W[1] = ( 3.0 * t3 - 6.0 * t2       + 4.0) / 6.0;
    W[2] = (-3.0 * t3 + 3.0 * t2 + 3.0 * t + 1.0) / 6.0;
    W[3] = t3 / 6.0;
}

/* scipy NI_EXTEND_NEAREST: out-of-range coefficient indices clamp to the
 * nearest edge sample (replicated edge). */
static inline long iclamp(long i, long n) {
    return i < 0 ? 0 : (i > n - 1 ? n - 1 : i);
}

/* ---------------------------------------------------------------------------
 * cs_sample3: batch-evaluate a cubic-spline COEFFICIENT array at n points.
 * Mirrors map_coordinates(C, [ys, xs], order=3, mode="nearest",
 * prefilter=False) — C is treated as B-spline coefficients (a scipy
 * spline_filter output), not node values.
 * ------------------------------------------------------------------------- */
void cs_sample3(const double *C, long ny, long nx,
                const double *ys, const double *xs, double *out, long n) {
    for (long i = 0; i < n; i++) {
        const double y = ys[i], x = xs[i];
        const double fy = floor(y), fx = floor(x);
        const long iy = (long)fy, ix = (long)fx;
        double Wy[4], Wx[4], r0, r1, r2, r3;
        const double *row;
        w3(y - fy, Wy);
        w3(x - fx, Wx);
        /* rows iy-1 .. iy+2, cols ix-1 .. ix+2, 4-tap dot per row         */
        row = C + iclamp(iy - 1, ny) * nx;
        r0 = Wx[0] * row[iclamp(ix - 1, nx)] + Wx[1] * row[iclamp(ix, nx)]
           + Wx[2] * row[iclamp(ix + 1, nx)] + Wx[3] * row[iclamp(ix + 2, nx)];
        row = C + iclamp(iy, ny) * nx;
        r1 = Wx[0] * row[iclamp(ix - 1, nx)] + Wx[1] * row[iclamp(ix, nx)]
           + Wx[2] * row[iclamp(ix + 1, nx)] + Wx[3] * row[iclamp(ix + 2, nx)];
        row = C + iclamp(iy + 1, ny) * nx;
        r2 = Wx[0] * row[iclamp(ix - 1, nx)] + Wx[1] * row[iclamp(ix, nx)]
           + Wx[2] * row[iclamp(ix + 1, nx)] + Wx[3] * row[iclamp(ix + 2, nx)];
        row = C + iclamp(iy + 2, ny) * nx;
        r3 = Wx[0] * row[iclamp(ix - 1, nx)] + Wx[1] * row[iclamp(ix, nx)]
           + Wx[2] * row[iclamp(ix + 1, nx)] + Wx[3] * row[iclamp(ix + 2, nx)];
        out[i] = Wy[0] * r0 + Wy[1] * r1 + Wy[2] * r2 + Wy[3] * r3;
    }
}

/* ---------------------------------------------------------------------------
 * cs_lk_step: one fused Lucas-Kanade Gauss-Newton accumulation pass.
 *
 * Given a prefiltered COEFFICIENT array C (ny x nx), n sample positions
 * (y0,x0) [already offset for any padding], and the current shift (cy,cx),
 * samples v = S(y0-cy, x0-cx) and central differences
 *   gy = (S(y+1,x) - S(y-1,x))/2,  gx = (S(y,x+1) - S(y,x-1))/2
 * exploiting shared taps (one 6x6 neighbourhood instead of five 4x4 ones).
 *
 * If w != NULL, the windowed model ref = w * img_warped is used:
 *   d = ref - w*v;  g = w*g   (window enters the gradients, not the image).
 * Otherwise d = ref - v, g as computed.
 *
 * out[5] accumulates the normal equations of A=[gy,gx], rhs=diff:
 *   out = [ sum(gy*gy), sum(gy*gx), sum(gx*gx), sum(gy*d), sum(gx*d) ]
 * The caller solves the 2x2 system (with its usual Tikhonov term) — keeping
 * LAPACK identical to the pure-numpy path by construction.
 * ------------------------------------------------------------------------- */
void cs_lk_step(const double *C, long ny, long nx,
                const double *ref, const double *w,
                const double *ys0, const double *xs0, long n,
                double cy, double cx, double *out) {
    double a = 0.0, b = 0.0, c = 0.0, d1 = 0.0, d2 = 0.0;
    for (long i = 0; i < n; i++) {
        const double y = ys0[i] - cy, x = xs0[i] - cx;
        const double fy = floor(y), fx = floor(x);
        const long iy = (long)fy, ix = (long)fx;
        const double ty = y - fy, tx = x - fx;
        double Wy[4], Wx[4];
        double blk[6][6];
        double R[6], Cx[6];
        double v, vpy, vmy, vpx, vmx, gy, gx, wi, d;
        int r, cc;
        w3(ty, Wy);
        w3(tx, Wx);
        /* 6x6 clamped neighbourhood: rows iy-2..iy+3, cols ix-2..ix+3      */
        for (r = 0; r < 6; r++) {
            const double *row = C + iclamp(iy - 2 + r, ny) * nx;
            for (cc = 0; cc < 6; cc++)
                blk[r][cc] = row[iclamp(ix - 2 + cc, nx)];
        }
        /* row projections at x-centre: rows iy-2..iy+3                      */
        for (r = 0; r < 6; r++)
            R[r] = Wx[0] * blk[r][1] + Wx[1] * blk[r][2]
                 + Wx[2] * blk[r][3] + Wx[3] * blk[r][4];
        v   = Wy[0] * R[1] + Wy[1] * R[2] + Wy[2] * R[3] + Wy[3] * R[4];
        vpy = Wy[0] * R[2] + Wy[1] * R[3] + Wy[2] * R[4] + Wy[3] * R[5];
        vmy = Wy[0] * R[0] + Wy[1] * R[1] + Wy[2] * R[2] + Wy[3] * R[3];
        /* column projections at y-centre (rows iy-1..iy+2): cols ix-2..+3 */
        for (cc = 0; cc < 6; cc++)
            Cx[cc] = Wy[0] * blk[1][cc] + Wy[1] * blk[2][cc]
                   + Wy[2] * blk[3][cc] + Wy[3] * blk[4][cc];
        vpx = Wx[0] * Cx[2] + Wx[1] * Cx[3] + Wx[2] * Cx[4] + Wx[3] * Cx[5];
        vmx = Wx[0] * Cx[0] + Wx[1] * Cx[1] + Wx[2] * Cx[2] + Wx[3] * Cx[3];
        gy = 0.5 * (vpy - vmy);
        gx = 0.5 * (vpx - vmx);
        wi = w ? w[i] : 1.0;
        d  = ref[i] - wi * v;
        gy *= wi;
        gx *= wi;
        a += gy * gy;
        b += gy * gx;
        c += gx * gx;
        d1 += gy * d;
        d2 += gx * d;
    }
    out[0] = a; out[1] = b; out[2] = c; out[3] = d1; out[4] = d2;
}

/* convenience used by the loader as a smoke check */
double cs_selfcheck(void) {
    /* B3 at node j of coefficients (…, 0, 1, 0, …) must equal 4/6. */
    double C[8] = {0, 0, 0, 1, 0, 0, 0, 0};
    double ys = 3.0, xs = 0.0, out = 0.0;
    cs_sample3(C, 8, 1, &ys, &xs, &out, 1);
    return out; /* expect 0.6666666666666666 */
}
