/*
 * grscore.c — native geometry core for the GRS metrology engine.
 *
 * Hot paths only. Everything here is a drop-in numerical equivalent of the
 * NumPy implementation in precision_engine.py; the Python side falls back
 * automatically when this module is not built, so the extension is optional
 * and the product still runs anywhere.
 *
 * Implemented:
 *   project_grid  — spheroid lon/lat grid -> pixel coords + LOS z (make_cylindrical)
 *   bilinear_map  — bilinear resample of an image at those coords
 *   limb_rays     — isophote ray-trace used by fit_limb_nav
 *
 * Rust was requested, but no Rust toolchain is reachable from this build
 * environment (sh.rustup.rs / static.rust-lang.org / crates.io all blocked and
 * no distro rustc), so this is C99 built with the stdlib setuptools path that
 * already ships with the app. Same goal, no new toolchain for end users.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

/* ---------------------------------------------------------------------- */
/* project_grid(width, height, xc, yc, a_eq_px, flattening, sub_lat_deg,
 *              north_pa_deg)
 *   -> (xs, ys, zlos) each (height, width) float64
 *
 * Mirrors lonlat_to_planet_xyz + planet_xyz_to_px:
 *   lon in [-90, 90], lat in [90, -90]
 *   r(phi) = 1/sqrt(cos^2 + (sin/k)^2)      (oblate spheroid surface)
 *   tilt by D about x, rotate by PA in sky plane, single isotropic scale.
 */
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
    for (int j = 0; j < height; ++j) {
        /* lats: linspace(90, -90, height) */
        const double lat = 90.0 - 180.0 * (double)j / (double)(height - 1);
        const double lr = lat * M_PI / 180.0;
        const double cl = cos(lr), sl = sin(lr);
        const double sk = sl / k;
        const double r = 1.0 / sqrt(cl * cl + sk * sk);
        const double ry = r * sl;      /* body y (north) */
        const double rc = r * cl;      /* equatorial component */

        for (int i = 0; i < width; ++i) {
            /* lons: linspace(-90, 90, width) */
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
/* bilinear_map(img, xs, ys, zlos, mu_min) -> out
 * img (h, w) float64 C-contiguous; xs/ys/zlos (H, W) float64.
 * Samples img at (ys, xs) where zlos > mu_min and the 2x2 stencil is in
 * bounds; elsewhere writes 0.0. Matches the NumPy `valid` mask exactly.
 */
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
    const npy_intp n = H * W;
    for (npy_intp t = 0; t < n; ++t) {
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
    Py_END_ALLOW_THREADS

    Py_DECREF(img); Py_DECREF(xs); Py_DECREF(ys); Py_DECREF(zl);
    return (PyObject *)out;
}

/* ---------------------------------------------------------------------- */
/* limb_rays(img, xc, yc, a, n_rays, n_rad, thr_frac, r_lo, r_hi)
 *   -> (pts_x, pts_y) float64 1-D, length = number of accepted rays.
 *
 * For each of n_rays angles, walks n_rad samples from r_lo*a to r_hi*a,
 * bilinearly sampling the image, then finds the outermost crossing of
 * thr_frac * (peak over the inner half) and refines it linearly. Identical
 * contract to the NumPy loop in fit_limb_nav.
 */
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
    npy_intp got = 0;

    Py_BEGIN_ALLOW_THREADS
    for (int k = 0; k < n_rays; ++k) {
        const double ang = 2.0 * M_PI * (double)k / (double)n_rays;
        const double ca = cos(ang), sa = sin(ang);

        for (int s = 0; s < n_rad; ++s) {
            const double rr = r0 + dr * (double)s;
            const double fx = xc + rr * ca, fy = yc + rr * sa;
            double flx = floor(fx), fly = floor(fy);
            npy_intp x0 = (npy_intp)flx, y0 = (npy_intp)fly;
            /* clamp exactly like np.clip(..., 0, w-2) */
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
        if (pmax <= 1e-12) continue;

        const double thr = thr_frac * pmax;
        int last = -1;
        for (int s = n_rad - 1; s >= 0; --s) {
            if (prof[s] >= thr) { last = s; break; }
        }

        double rad;
        if (last < 0) {
            /* steepest descent fallback (np.gradient argmin) */
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

        ox[got] = xc + rad * ca;
        oy[got] = yc + rad * sa;
        ++got;
    }
    Py_END_ALLOW_THREADS

    npy_intp dn[1] = {got};
    PyArrayObject *ax = (PyArrayObject *)PyArray_SimpleNew(1, dn, NPY_DOUBLE);
    PyArrayObject *ay = (PyArrayObject *)PyArray_SimpleNew(1, dn, NPY_DOUBLE);
    if (!ax || !ay) {
        Py_XDECREF(ax); Py_XDECREF(ay);
        PyMem_Free(prof); PyMem_Free(ox); PyMem_Free(oy);
        Py_DECREF(img);
        return NULL;
    }
    memcpy(PyArray_DATA(ax), ox, (size_t)got * sizeof(double));
    memcpy(PyArray_DATA(ay), oy, (size_t)got * sizeof(double));

    PyMem_Free(prof); PyMem_Free(ox); PyMem_Free(oy);
    Py_DECREF(img);
    return Py_BuildValue("NN", (PyObject *)ax, (PyObject *)ay);
}

/* ---------------------------------------------------------------------- */
static PyMethodDef GrsMethods[] = {
    {"project_grid", grscore_project_grid, METH_VARARGS,
     "project_grid(w,h,xc,yc,a_eq,flat,sub_lat,pa) -> (xs,ys,zlos)"},
    {"bilinear_map", grscore_bilinear_map, METH_VARARGS,
     "bilinear_map(img,xs,ys,zlos,mu_min) -> out"},
    {"limb_rays", grscore_limb_rays, METH_VARARGS,
     "limb_rays(img,xc,yc,a,n_rays,n_rad,thr_frac,r_lo,r_hi) -> (px,py)"},
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
