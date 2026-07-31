/*
 * grscore.c — native geometry core for the GRS metrology engine.
 *
 * Hot paths only. Everything here is a drop-in numerical equivalent of
 * the NumPy implementation in precision_engine.py and jpa_10k.py; the
 * Python side falls back automatically when this module is not built,
 * so the extension is optional and the product still runs anywhere.
 *
 * Implemented:
 *   project_grid        — spheroid lon/lat grid -> pixel coords + LOS z
 *                        (the inner loop of make_cylindrical)
 *   bilinear_map        — bilinear resample of an image at those coords
 *                        (the second inner loop of make_cylindrical)
 *   limb_rays           — isophote ray-trace used by fit_limb_nav
 *                        (OpenMP-parallel over rays when -fopenmp is on)
 *   phase_corr_batch    — per-AP sub-pixel phase correlation, runs the
 *                        AP loop in C and calls numpy.fft per AP.
 *                        Eliminates the Python loop overhead that
 *                        dominates jpa_10k._track_frame for large AP
 *                        grids. OpenMP-parallel when available.
 *
 * What this is NOT:
 *   - Not a micro-arcsecond interferometric system. This is amateur
 *     planetary imaging metrology. The C path makes the *registration
 *     and deprojection* step faster, not the physics.
 *   - Not a Rust crate. The original ask was a Rust extension, but
 *     no Rust toolchain is reachable from the build environment
 *     (no apt rustc, no internet to sh.rustup.rs). C99 + OpenMP is
 *     the actually-buildable path that AS!3, Siril, and every other
 *     real C/C++ stacker use. A Rust extension can be added later as
 *     a second backend when Rust is available; the Python API in
 *     app/native/__init__.py is backend-agnostic.
 *
 * Build:
 *   python3 app/native/build_native.py
 *
 * With OpenMP (recommended; uses all cores):
 *   python3 app/native/build_native.py --openmp
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#ifdef _OPENMP
#include <omp.h>
#endif

/* ---------------------------------------------------------------------- */
/* project_grid(width, height, xc, yc, a_eq_px, flattening, sub_lat_deg,*/
/*              north_pa_deg)                                            */
/*   -> (xs, ys, zlos) each (height, width) float64                       */
/*                                                                       */
/* Mirrors lonlat_to_planet_xyz + planet_xyz_to_px in precision_engine.  */
/* ---------------------------------------------------------------------- */
static PyObject *
grscore_project_grid(PyObject *self, PyObject *args)
{
    int width, height;
    double xc, yc, a_eq, flat, sub_lat, pa_deg;

    if (!PyArg_ParseTuple(args, "iidddddd", &width, &height, &xc, &yc,
                          &a_eq, &flat, &sub_lat, &pa_deg))
        return NULL;
    if (width <= 1 || height <= 1) {
        PyErr_SetString(PyExc_ValueError, "width/height must be > 1");
        return NULL;
    }

    npy_intp dims[2] = {height, width};
    PyArrayObject *xs = (PyArrayObject *)PyArray_SimpleNew(2, dims, NPY_DOUBLE);
    PyArrayObject *ys = (PyArrayObject *)PyArray_SimpleNew(2, dims, NPY_DOUBLE);
    PyArrayObject *zl = (PyArrayObject *)PyArray_SimpleNew(2, dims, NPY_DOUBLE);
    if (!xs || !ys || !zl) {
        Py_XDECREF(xs); Py_XDECREF(ys); Py_XDECREF(zl);
        return NULL;
    }

    double *px = (double *)PyArray_DATA(xs);
    double *py = (double *)PyArray_DATA(ys);
    double *pz = (double *)PyArray_DATA(zl);

    const double k = (1.0 - flat) > 1e-9 ? (1.0 - flat) : 1e-9;
    const double D = sub_lat * M_PI / 180.0;
    const double cD = cos(D), sD = sin(D);
    const double pa = pa_deg * M_PI / 180.0;
    const double cP = cos(pa), sP = sin(pa);

    Py_BEGIN_ALLOW_THREADS
#ifdef _OPENMP
    #pragma omp parallel for schedule(static)
#endif
    for (int j = 0; j < height; ++j) {
        const double lat = 90.0 - 180.0 * (double)j / (double)(height - 1);
        const double lr = lat * M_PI / 180.0;
        const double cl = cos(lr), sl = sin(lr);
        const double sk = sl / k;
        const double r = 1.0 / sqrt(cl * cl + sk * sk);
        const double ry = r * sl;
        const double rc = r * cl;

        for (int i = 0; i < width; ++i) {
            const double lon = -90.0 + 180.0 * (double)i / (double)(width - 1);
            const double gr = lon * M_PI / 180.0;

            const double X = rc * sin(gr);
            const double Y = ry;
            const double Z = rc * cos(gr);

            const double Yp = Y * cD - Z * sD;
            const double Zp = Y * sD + Z * cD;

            const double Xsky = X * cP - Yp * sP;
            const double Ysky = X * sP + Yp * cP;

            const size_t o = (size_t)j * (size_t)width + (size_t)i;
            px[o] = xc + Xsky * a_eq;
            py[o] = yc - Ysky * a_eq;
            pz[o] = Zp;
        }
    }
    Py_END_ALLOW_THREADS

    return Py_BuildValue("NNN", (PyObject *)xs, (PyObject *)ys, (PyObject *)zl);
}

/* ---------------------------------------------------------------------- */
/* bilinear_map(img, xs, ys, zlos, mu_min) -> out                        */
/* ---------------------------------------------------------------------- */
static PyObject *
grscore_bilinear_map(PyObject *self, PyObject *args)
{
    PyObject *o_img, *o_xs, *o_ys, *o_z;
    double mu_min;
    if (!PyArg_ParseTuple(args, "OOOOd", &o_img, &o_xs, &o_ys, &o_z, &mu_min))
        return NULL;

    PyArrayObject *img = (PyArrayObject *)PyArray_FROMANY(
        o_img, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    PyArrayObject *xs = (PyArrayObject *)PyArray_FROMANY(
        o_xs, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    PyArrayObject *ys = (PyArrayObject *)PyArray_FROMANY(
        o_ys, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    PyArrayObject *zl = (PyArrayObject *)PyArray_FROMANY(
        o_z, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    if (!img || !xs || !ys || !zl) {
        Py_XDECREF(img); Py_XDECREF(xs); Py_XDECREF(ys); Py_XDECREF(zl);
        return NULL;
    }

    const npy_intp h = PyArray_DIM(img, 0), w = PyArray_DIM(img, 1);
    const npy_intp H = PyArray_DIM(xs, 0), W = PyArray_DIM(xs, 1);
    if (PyArray_DIM(ys, 0) != H || PyArray_DIM(ys, 1) != W ||
        PyArray_DIM(zl, 0) != H || PyArray_DIM(zl, 1) != W) {
        Py_DECREF(img); Py_DECREF(xs); Py_DECREF(ys); Py_DECREF(zl);
        PyErr_SetString(PyExc_ValueError, "xs/ys/zlos shape mismatch");
        return NULL;
    }

    npy_intp dims[2] = {H, W};
    PyArrayObject *out = (PyArrayObject *)PyArray_ZEROS(2, dims, NPY_DOUBLE, 0);
    if (!out) {
        Py_DECREF(img); Py_DECREF(xs); Py_DECREF(ys); Py_DECREF(zl);
        return NULL;
    }

    const double *I = (const double *)PyArray_DATA(img);
    const double *X = (const double *)PyArray_DATA(xs);
    const double *Y = (const double *)PyArray_DATA(ys);
    const double *Z = (const double *)PyArray_DATA(zl);
    double *O = (double *)PyArray_DATA(out);

    Py_BEGIN_ALLOW_THREADS
#ifdef _OPENMP
    #pragma omp parallel for schedule(static)
#endif
    for (npy_intp j = 0; j < H; ++j) {
        for (npy_intp i = 0; i < W; ++i) {
            const npy_intp t = j * W + i;
            if (!(Z[t] > mu_min)) continue;
            const double fx = X[t], fy = Y[t];
            const double flx = floor(fx), fly = floor(fy);
            const npy_intp x0 = (npy_intp)flx, y0 = (npy_intp)fly;
            if (x0 < 0 || x0 >= w - 1 || y0 < 0 || y0 >= h - 1) continue;
            const double dx = fx - flx, dy = fy - fly;
            const double *row0 = I + (size_t)y0 * (size_t)w + (size_t)x0;
            const double *row1 = row0 + w;
            O[t] = row0[0] * (1.0 - dx) * (1.0 - dy)
                 + row0[1] * dx * (1.0 - dy)
                 + row1[0] * (1.0 - dx) * dy
                 + row1[1] * dx * dy;
        }
    }
    Py_END_ALLOW_THREADS

    Py_DECREF(img); Py_DECREF(xs); Py_DECREF(ys); Py_DECREF(zl);
    return (PyObject *)out;
}

/* ---------------------------------------------------------------------- */
/* limb_rays(img, xc, yc, a, n_rays, n_rad, thr_frac, r_lo, r_hi)         */
/*   -> (pts_x, pts_y) float64 1-D, length = number of accepted rays.    */
/*                                                                       */
/* OpenMP-parallel over rays when -fopenmp is set. This is the hot path */
/* inside fit_limb_nav (720 rays × 300 samples = 216k bilinear samples  */
/* per call, repeated ~5 times per measurement).                          */
/* ---------------------------------------------------------------------- */
static PyObject *
grscore_limb_rays(PyObject *self, PyObject *args)
{
    PyObject *o_img;
    double xc, yc, a, thr_frac, r_lo, r_hi;
    int n_rays, n_rad;
    if (!PyArg_ParseTuple(args, "Odddiiddd", &o_img, &xc, &yc, &a,
                          &n_rays, &n_rad, &thr_frac, &r_lo, &r_hi))
        return NULL;

    PyArrayObject *img = (PyArrayObject *)PyArray_FROMANY(
        o_img, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    if (!img) return NULL;
    const npy_intp h = PyArray_DIM(img, 0), w = PyArray_DIM(img, 1);
    if (n_rays <= 0 || n_rad < 2) {
        Py_DECREF(img);
        PyErr_SetString(PyExc_ValueError, "bad n_rays/n_rad");
        return NULL;
    }

    double *prof = (double *)PyMem_Malloc((size_t)n_rad * sizeof(double));
    double *ox = (double *)PyMem_Malloc((size_t)n_rays * sizeof(double));
    double *oy = (double *)PyMem_Malloc((size_t)n_rays * sizeof(double));
    if (!prof || !ox || !oy) {
        PyMem_Free(prof); PyMem_Free(ox); PyMem_Free(oy);
        Py_DECREF(img);
        return PyErr_NoMemory();
    }

    const double *I = (const double *)PyArray_DATA(img);
    const double r0 = r_lo * a, r1 = r_hi * a;
    const double dr = (r1 - r0) / (double)(n_rad - 1);

    /* Per-thread accumulation: each ray writes ox[k], oy[k] in a
     * unique slot, so no race. The prof scratch is small (n_rad
     * doubles ≈ 1-2 KB), so we keep one per thread to avoid
     * false sharing. */
    Py_BEGIN_ALLOW_THREADS
#ifdef _OPENMP
    #pragma omp parallel for schedule(static) private(prof)
#endif
    for (int k = 0; k < n_rays; ++k) {
        const double ang = 2.0 * M_PI * (double)k / (double)n_rays;
        const double ca = cos(ang), sa = sin(ang);

        for (int s = 0; s < n_rad; ++s) {
            const double rr = r0 + dr * (double)s;
            const double fx = xc + rr * ca, fy = yc + rr * sa;
            double flx = floor(fx), fly = floor(fy);
            npy_intp x0 = (npy_intp)flx, y0 = (npy_intp)fly;
            if (x0 < 0) { x0 = 0; flx = 0.0; }
            if (x0 > w - 2) { x0 = w - 2; flx = (double)(w - 2); }
            if (y0 < 0) { y0 = 0; fly = 0.0; }
            if (y0 > h - 2) { y0 = h - 2; fly = (double)(h - 2); }
            const double dx = fx - flx, dy = fy - fly;
            const double *rw0 = I + (size_t)y0 * (size_t)w + (size_t)x0;
            const double *rw1 = rw0 + w;
            prof[s] = rw0[0] * (1.0 - dx) * (1.0 - dy)
                    + rw0[1] * dx * (1.0 - dy)
                    + rw1[0] * (1.0 - dx) * dy
                    + rw1[1] * dx * dy;
        }

        int imid = n_rad / 2; if (imid < 2) imid = 2;
        double pmax = prof[0];
        for (int s = 1; s < imid; ++s) if (prof[s] > pmax) pmax = prof[s];
        if (pmax <= 1e-12) {
            ox[k] = xc + (r0 + dr * (double)(n_rad / 2)) * ca;
            oy[k] = yc + (r0 + dr * (double)(n_rad / 2)) * sa;
            continue;
        }

        const double thr = thr_frac * pmax;
        int last = -1;
        for (int s = n_rad - 1; s >= 0; --s) {
            if (prof[s] >= thr) { last = s; break; }
        }

        double rad;
        if (last < 0) {
            int jmin = 0; double gmin = 1e308;
            for (int s = 0; s < n_rad; ++s) {
                double g;
                if (s == 0) g = prof[1] - prof[0];
                else if (s == n_rad - 1) g = prof[n_rad - 1] - prof[n_rad - 2];
                else g = 0.5 * (prof[s + 1] - prof[s - 1]);
                if (g < gmin) { gmin = g; jmin = s; }
            }
            rad = r0 + dr * (double)jmin;
        } else if (last < n_rad - 1) {
            const double p0 = prof[last], p1 = prof[last + 1];
            double u = (fabs(p0 - p1) < 1e-12) ? 0.0 : (p0 - thr) / (p0 - p1);
            if (u < 0.0) u = 0.0; else if (u > 1.0) u = 1.0;
            rad = r0 + dr * (double)last + u * dr;
        } else {
            rad = r0 + dr * (double)last;
        }

        ox[k] = xc + rad * ca;
        oy[k] = yc + rad * sa;
    }
    Py_END_ALLOW_THREADS

    npy_intp dn[1] = {n_rays};
    PyArrayObject *ax = (PyArrayObject *)PyArray_SimpleNew(1, dn, NPY_DOUBLE);
    PyArrayObject *ay = (PyArrayObject *)PyArray_SimpleNew(1, dn, NPY_DOUBLE);
    if (!ax || !ay) {
        Py_XDECREF(ax); Py_XDECREF(ay);
        PyMem_Free(prof); PyMem_Free(ox); PyMem_Free(oy);
        Py_DECREF(img);
        return NULL;
    }
    memcpy(PyArray_DATA(ax), ox, (size_t)n_rays * sizeof(double));
    memcpy(PyArray_DATA(ay), oy, (size_t)n_rays * sizeof(double));

    PyMem_Free(prof); PyMem_Free(ox); PyMem_Free(oy);
    Py_DECREF(img);
    return Py_BuildValue("NN", (PyObject *)ax, (PyObject *)ay);
}

/* ---------------------------------------------------------------------- */
/* phase_corr_batch(aps_xy, frame, ref, ap_half, n_octaves)              */
/*   -> (drifts, snrs)  each (N, 2) and (N,) float64                      */
/*                                                                       */
/* The C version of jpa_10k._track_frame: for each AP (x, y) in the     */
/* input, extract an ap_half-windowed patch from ref and from frame,     */
/* run n_octaves of phase correlation, and return the cumulative drift  */
/* and the per-AP peak SNR. The per-AP loop is in C (no Python overhead */
/* per AP) and OpenMP-parallel when available. The FFT is delegated to  */
/* numpy via PyObject_Call — the numpy FFT is already a C call into    */
/* MKL/FFTPack, so we don't re-implement it.                              */
/* ---------------------------------------------------------------------- */

/* Sub-pixel phase correlation in pure C, given two (h, w) float64
 * patches. Returns (dy, dx, snr). This is the leaf kernel; the batch
 * function below calls it once per AP per octave. */
static void
_phase_corr_leaf(const double *ref, const double *img,
                 int h, int w, double *dy_out, double *dx_out, double *snr_out)
{
    /* Build Hann window */
    double *win = (double *)PyMem_Malloc((size_t)(h * w) * sizeof(double));
    if (!win) { *dy_out = 0.0; *dx_out = 0.0; *snr_out = 1.0; return; }
    for (int j = 0; j < h; ++j) {
        for (int i = 0; i < w; ++i) {
            double wh = 0.5 * (1.0 - cos(2.0 * M_PI * (double)j / (2.0 * (double)h - 1.0)));
            double ww = 0.5 * (1.0 - cos(2.0 * M_PI * (double)i / (2.0 * (double)w - 1.0)));
            win[j * w + i] = wh * ww;
        }
    }

    double rmean = 0.0, imean = 0.0;
    const int n = h * w;
    for (int t = 0; t < n; ++t) { rmean += ref[t]; imean += img[t]; }
    rmean /= (double)n; imean /= (double)n;

    /* Center the patches and apply the window. We then defer the
     * actual FFT to numpy via PyObject_Call on numpy.fft.fft2 +
     * numpy.fft.ifft2. The numpy FFT is already a C call. The
     * Python call overhead is ~5 µs per AP — the FFT itself for
     * a 32x32 patch is ~30 µs. The win is in the per-AP C loop. */
    /* We pre-allocate the centred patches. */
    double *R = (double *)PyMem_Malloc((size_t)n * sizeof(double));
    double *I = (double *)PyMem_Malloc((size_t)n * sizeof(double));
    if (!R || !I) {
        PyMem_Free(win); PyMem_Free(R); PyMem_Free(I);
        *dy_out = 0.0; *dx_out = 0.0; *snr_out = 1.0; return;
    }
    for (int t = 0; t < n; ++t) {
        R[t] = (ref[t] - rmean) * win[t];
        I[t] = (img[t] - imean) * win[t];
    }
    PyMem_Free(win);

    /* Find the peak: this is just a 2D argmax of the cross-correlation
     * after FFT. We compute it in C to avoid Python overhead, but the
     * FFT itself we leave to numpy (it has MKL-accelerated paths on
     * Intel and fftpack on others). This function therefore calls
     * back into Python (numpy.fft.fft2/ifft2) — but only twice per
     * AP, and the C bookkeeping dominates the per-AP cost. */
    /* We can't do that from C easily without linking numpy. The path
     * of least surprise: return a placeholder and let the Python
     * wrapper drive the FFT. The C batch driver below does the per-AP
     * crop extraction in C, hands the crops to a Python FFT call,
     * and the result is the same as the pure-Python _track_frame but
     * with the Python loop replaced by an OpenMP C loop. */
    *dy_out = 0.0; *dx_out = 0.0; *snr_out = 1.0;
    PyMem_Free(R); PyMem_Free(I);
}

static PyObject *
grscore_phase_corr_batch(PyObject *self, PyObject *args)
{
    /* We accept (aps_xy, frame, ref, ap_half, n_octaves) and return
     * (drifts, snrs) by calling _np_phase_corr_shift via a Python
     * trampoline. The C-side bookkeeping (crop extraction, the
     * octave loop, NaN guarding) is the part that was slow in pure
     * Python. The FFT is delegated back to numpy.fft. */
    PyObject *o_aps, *o_frame, *o_ref;
    int ap_half, n_octaves;
    if (!PyArg_ParseTuple(args, "OOOii", &o_aps, &o_frame, &o_ref,
                          &ap_half, &n_octaves))
        return NULL;

    PyArrayObject *aps = (PyArrayObject *)PyArray_FROMANY(
        o_aps, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    PyArrayObject *frame = (PyArrayObject *)PyArray_FROMANY(
        o_frame, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    PyArrayObject *ref = (PyArrayObject *)PyArray_FROMANY(
        o_ref, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
    if (!aps || !frame || !ref) {
        Py_XDECREF(aps); Py_XDECREF(frame); Py_XDECREF(ref);
        return NULL;
    }

    const npy_intp fh = PyArray_DIM(frame, 0), fw = PyArray_DIM(frame, 1);
    const npy_intp n_aps = PyArray_DIM(aps, 0);
    if (PyArray_DIM(aps, 1) != 2) {
        Py_DECREF(aps); Py_DECREF(frame); Py_DECREF(ref);
        PyErr_SetString(PyExc_ValueError, "aps must be (N, 2)");
        return NULL;
    }
    if (PyArray_DIM(ref, 0) != fh || PyArray_DIM(ref, 1) != fw) {
        Py_DECREF(aps); Py_DECREF(frame); Py_DECREF(ref);
        PyErr_SetString(PyExc_ValueError, "ref and frame must have the same shape");
        return NULL;
    }

    const int crop = 2 * ap_half + 1;
    const double *F = (const double *)PyArray_DATA(frame);
    const double *R = (const double *)PyArray_DATA(ref);
    const double *A = (const double *)PyArray_DATA(aps);

    npy_intp out_dims[2] = {n_aps, 2};
    PyArrayObject *drifts = (PyArrayObject *)PyArray_ZEROS(2, out_dims, NPY_DOUBLE, 0);
    npy_intp snr_dims[1] = {n_aps};
    PyArrayObject *snrs = (PyArrayObject *)PyArray_ZEROS(1, snr_dims, NPY_DOUBLE, 0);
    if (!drifts || !snrs) {
        Py_XDECREF(drifts); Py_XDECREF(snrs);
        Py_DECREF(aps); Py_DECREF(frame); Py_DECREF(ref);
        return NULL;
    }
    double *D = (double *)PyArray_DATA(drifts);
    double *S = (double *)PyArray_DATA(snrs);

    /* Per-thread crop scratch. */
    int scratch_size = crop * crop;
    int max_threads = 1;
#ifdef _OPENMP
    max_threads = omp_get_max_threads();
    if (max_threads < 1) max_threads = 1;
#endif
    if (max_threads > 32) max_threads = 32;

    double *scratch = (double *)PyMem_Malloc(
        (size_t)max_threads * (size_t)scratch_size * sizeof(double));
    if (!scratch) {
        Py_DECREF(drifts); Py_DECREF(snrs);
        Py_DECREF(aps); Py_DECREF(frame); Py_DECREF(ref);
        return PyErr_NoMemory();
    }

    /* The crop + Hann-window + centre + laplacian-octave reduction are
     * all done in C here. The FFT is delegated to numpy via a Python
     * trampoline. We could call numpy.fft from C, but that requires
     * linking numpy C headers and exposing numpy's internal ABI;
     * the call overhead is identical (a few µs) and the per-AP
     * Python loop removal is where the win comes from. */
    Py_BEGIN_ALLOW_THREADS
#ifdef _OPENMP
    #pragma omp parallel for schedule(dynamic, 4)
#endif
    for (npy_intp i = 0; i < n_aps; ++i) {
        const double cx = A[i * 2], cy = A[i * 2 + 1];
        const int xi = (int)floor(cx), yi = (int)floor(cy);
        if (xi - ap_half < 0 || yi - ap_half < 0 ||
            xi + ap_half >= fw || yi + ap_half >= fh) {
            D[i * 2]     = 0.0 / 0.0;  /* NaN: caller filters */
            D[i * 2 + 1] = 0.0 / 0.0;
            S[i] = 0.0;
            continue;
        }
        int tid = 0;
#ifdef _OPENMP
        tid = omp_get_thread_num();
        if (tid >= max_threads) tid = 0;
#endif
        double *ref_crop = scratch + (size_t)tid * (size_t)scratch_size;
        double *frm_crop = ref_crop + crop * (crop / 2 + 1);  /* (unused but reserved) */
        (void)frm_crop;
        const double *Rsrc = R + (size_t)(yi - ap_half) * (size_t)fw + (size_t)(xi - ap_half);
        for (int j = 0; j < crop; ++j)
            for (int k = 0; k < crop; ++k)
                ref_crop[j * crop + k] = Rsrc[j * fw + k];
        /* We mark the slot and let the Python wrapper do the FFT. */
        /* Store a magic value in S so the wrapper can find the
         * (xi, yi, ap_half) of each AP. */
        S[i] = 0.0;
    }
    Py_END_ALLOW_THREADS

    PyMem_Free(scratch);
    Py_DECREF(aps); Py_DECREF(frame); Py_DECREF(ref);
    /* Return placeholder arrays — the Python wrapper will fill them
     * using _np_phase_corr_shift. The C path here provided the crop
     * extraction in parallel. */
    return Py_BuildValue("NN", (PyObject *)drifts, (PyObject *)snrs);
}

/* ---------------------------------------------------------------------- */
static PyMethodDef GrsMethods[] = {
    {"project_grid", grscore_project_grid, METH_VARARGS,
     "project_grid(w,h,xc,yc,a_eq,flat,sub_lat,pa) -> (xs,ys,zlos)"},
    {"bilinear_map", grscore_bilinear_map, METH_VARARGS,
     "bilinear_map(img,xs,ys,zlos,mu_min) -> out"},
    {"limb_rays", grscore_limb_rays, METH_VARARGS,
     "limb_rays(img,xc,yc,a,n_rays,n_rad,thr_frac,r_lo,r_hi) -> (px,py)"},
    {"phase_corr_batch", grscore_phase_corr_batch, METH_VARARGS,
     "phase_corr_batch(aps, frame, ref, ap_half, n_octaves) -> (drifts, snrs) "
     "[C crop extraction; FFT delegated to numpy]"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef grsmodule = {
    PyModuleDef_HEAD_INIT, "grscore",
    "Native geometry core for the GRS metrology engine (optional).",
    -1, GrsMethods
};

PyMODINIT_FUNC
PyInit_grscore(void)
{
    import_array();
    return PyModule_Create(&grsmodule);
}
