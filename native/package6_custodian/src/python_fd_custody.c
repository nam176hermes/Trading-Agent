#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

/*
 * The Python object exists, with its native deallocator installed, before open(2)
 * or openat(2) can produce a descriptor.  The descriptor is never returned as
 * an unowned Python integer.  close ownership is consumed before the one close
 * syscall, including ambiguous failure paths.
 */

typedef struct {
    PyObject_HEAD
    int fd;
    uint64_t device;
    uint64_t inode;
} P6FdOwner;

static PyTypeObject P6FdOwnerType;
static Py_ssize_t p6_close_call_count = 0;

#ifdef P6FD_TESTING
static int p6_fail_after_open_once = 0;
static int p6_fail_close_after_reuse_once = 0;
static int p6_replacement_fd = -1;
#endif

static int
p6_owner_consume_and_close(P6FdOwner *owner)
{
    int descriptor;
    int close_result;

    descriptor = owner->fd;
    if (descriptor < 0) {
        return 1;
    }

    owner->fd = -1;
    p6_close_call_count += 1;

#ifdef P6FD_TESTING
    if (p6_fail_close_after_reuse_once != 0) {
        int replacement;

        p6_fail_close_after_reuse_once = 0;
        replacement = p6_replacement_fd;
        p6_replacement_fd = -1;
        close_result = close(descriptor);
        if (close_result != 0) {
            return 0;
        }
        if (dup2(replacement, descriptor) < 0) {
            return 0;
        }
        errno = EINTR;
        return 0;
    }
#endif

    close_result = close(descriptor);
    return close_result == 0;
}

static void
p6_owner_dealloc(P6FdOwner *owner)
{
    (void)p6_owner_consume_and_close(owner);
    Py_TYPE(owner)->tp_free((PyObject *)owner);
}

static PyObject *
p6_owner_close(P6FdOwner *owner, PyObject *Py_UNUSED(ignored))
{
    if (p6_owner_consume_and_close(owner) != 0) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject *
p6_owner_abandon(P6FdOwner *owner, PyObject *Py_UNUSED(ignored))
{
    owner->fd = -1;
    Py_RETURN_NONE;
}

static PyObject *
p6_owner_get_descriptor(P6FdOwner *owner, void *Py_UNUSED(closure))
{
    return PyLong_FromLong((long)owner->fd);
}

static PyObject *
p6_owner_get_identity(P6FdOwner *owner, void *Py_UNUSED(closure))
{
    PyObject *device;
    PyObject *inode;
    PyObject *identity;

    device = PyLong_FromUnsignedLongLong((unsigned long long)owner->device);
    if (device == NULL) {
        return NULL;
    }
    inode = PyLong_FromUnsignedLongLong((unsigned long long)owner->inode);
    if (inode == NULL) {
        Py_DECREF(device);
        return NULL;
    }
    identity = PyTuple_Pack(2, device, inode);
    Py_DECREF(device);
    Py_DECREF(inode);
    return identity;
}

static PyMethodDef p6_owner_methods[] = {
    {"close", (PyCFunction)p6_owner_close, METH_NOARGS,
     PyDoc_STR("Consume ownership and issue at most one close syscall.")},
    {"abandon_uncertain_generation", (PyCFunction)p6_owner_abandon, METH_NOARGS,
     PyDoc_STR("Consume ownership without closing a possibly reused number.")},
    {NULL, NULL, 0, NULL},
};

static PyGetSetDef p6_owner_getset[] = {
    {"descriptor", (getter)p6_owner_get_descriptor, NULL,
     PyDoc_STR("Borrowed descriptor number while this owner is alive."), NULL},
    {"identity", (getter)p6_owner_get_identity, NULL,
     PyDoc_STR("Device and inode captured before returning to Python."), NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyTypeObject P6FdOwnerType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_package6_fd_custody.FdOwner",
    .tp_basicsize = (Py_ssize_t)sizeof(P6FdOwner),
    .tp_dealloc = (destructor)p6_owner_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = PyDoc_STR("Native exactly-once descriptor owner."),
    .tp_methods = p6_owner_methods,
    .tp_getset = p6_owner_getset,
};

static P6FdOwner *
p6_owner_new_unopened(void)
{
    P6FdOwner *owner;

    owner = PyObject_New(P6FdOwner, &P6FdOwnerType);
    if (owner == NULL) {
        return NULL;
    }
    owner->fd = -1;
    owner->device = 0;
    owner->inode = 0;
    return owner;
}

static PyObject *
p6_owner_finish_open(P6FdOwner *owner, int descriptor, PyObject *path)
{
    struct stat info;
    int saved_errno;

    if (descriptor < 0) {
        saved_errno = errno;
        Py_DECREF(owner);
        errno = saved_errno;
        return PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
    }

    owner->fd = descriptor;
    if (fstat(descriptor, &info) != 0) {
        saved_errno = errno;
        Py_DECREF(owner);
        errno = saved_errno;
        return PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
    }
    owner->device = (uint64_t)info.st_dev;
    owner->inode = (uint64_t)info.st_ino;

#ifdef P6FD_TESTING
    if (p6_fail_after_open_once != 0) {
        p6_fail_after_open_once = 0;
        PyErr_SetString(PyExc_RuntimeError, "injected native ownership fault");
        Py_DECREF(owner);
        return NULL;
    }
#endif

    return (PyObject *)owner;
}

static int
p6_validate_path(PyObject *path, const char **raw_path, Py_ssize_t *path_size)
{
    char *mutable_path;

    if (PyBytes_AsStringAndSize(path, &mutable_path, path_size) != 0) {
        return 0;
    }
    if (memchr(mutable_path, '\0', (size_t)*path_size) != NULL) {
        PyErr_SetString(PyExc_ValueError, "descriptor path contains NUL");
        return 0;
    }
    *raw_path = mutable_path;
    return 1;
}

static PyObject *
p6_open(PyObject *Py_UNUSED(module), PyObject *args)
{
    PyObject *path;
    const char *raw_path;
    Py_ssize_t path_size;
    int flags;
    unsigned int mode;
    int descriptor;
    P6FdOwner *owner;

    if (!PyArg_ParseTuple(args, "O!iI:open", &PyBytes_Type, &path, &flags, &mode)) {
        return NULL;
    }
    if (p6_validate_path(path, &raw_path, &path_size) == 0) {
        return NULL;
    }
    (void)path_size;
    owner = p6_owner_new_unopened();
    if (owner == NULL) {
        return NULL;
    }
    descriptor = open(raw_path, flags | O_CLOEXEC, (mode_t)mode);
    return p6_owner_finish_open(owner, descriptor, path);
}

static PyObject *
p6_openat(PyObject *Py_UNUSED(module), PyObject *args)
{
    int directory;
    PyObject *path;
    const char *raw_path;
    Py_ssize_t path_size;
    int flags;
    unsigned int mode;
    int descriptor;
    P6FdOwner *owner;

    if (!PyArg_ParseTuple(
            args,
            "iO!iI:openat",
            &directory,
            &PyBytes_Type,
            &path,
            &flags,
            &mode)) {
        return NULL;
    }
    if (p6_validate_path(path, &raw_path, &path_size) == 0) {
        return NULL;
    }
    (void)path_size;
    owner = p6_owner_new_unopened();
    if (owner == NULL) {
        return NULL;
    }
    descriptor = openat(directory, raw_path, flags | O_CLOEXEC, (mode_t)mode);
    return p6_owner_finish_open(owner, descriptor, path);
}

#ifdef P6FD_TESTING
static PyObject *
p6_test_reset(PyObject *Py_UNUSED(module), PyObject *Py_UNUSED(ignored))
{
    p6_fail_after_open_once = 0;
    p6_fail_close_after_reuse_once = 0;
    p6_replacement_fd = -1;
    p6_close_call_count = 0;
    Py_RETURN_NONE;
}

static PyObject *
p6_test_fail_after_open_once_method(
    PyObject *Py_UNUSED(module),
    PyObject *Py_UNUSED(ignored))
{
    p6_fail_after_open_once = 1;
    Py_RETURN_NONE;
}

static PyObject *
p6_test_close_call_count_method(
    PyObject *Py_UNUSED(module),
    PyObject *Py_UNUSED(ignored))
{
    return PyLong_FromSsize_t(p6_close_call_count);
}

static PyObject *
p6_test_fail_close_after_reuse_once_method(
    PyObject *Py_UNUSED(module),
    PyObject *args)
{
    int replacement;

    if (!PyArg_ParseTuple(args, "i:_test_fail_close_after_reuse_once", &replacement)) {
        return NULL;
    }
    if (replacement < 0) {
        PyErr_SetString(PyExc_ValueError, "replacement descriptor must be nonnegative");
        return NULL;
    }
    p6_fail_close_after_reuse_once = 1;
    p6_replacement_fd = replacement;
    Py_RETURN_NONE;
}
#endif

static PyMethodDef p6_module_methods[] = {
    {"open", (PyCFunction)p6_open, METH_VARARGS,
     PyDoc_STR("Open a path and return a native descriptor owner.")},
    {"openat", (PyCFunction)p6_openat, METH_VARARGS,
     PyDoc_STR("Open relative to a directory and return a native owner.")},
#ifdef P6FD_TESTING
    {"_test_reset", (PyCFunction)p6_test_reset, METH_NOARGS, NULL},
    {"_test_fail_after_open_once", (PyCFunction)p6_test_fail_after_open_once_method,
     METH_NOARGS, NULL},
    {"_test_close_call_count", (PyCFunction)p6_test_close_call_count_method,
     METH_NOARGS, NULL},
    {"_test_fail_close_after_reuse_once",
     (PyCFunction)p6_test_fail_close_after_reuse_once_method, METH_VARARGS, NULL},
#endif
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef p6_module = {
    PyModuleDef_HEAD_INIT,
    "_package6_fd_custody",
    "Native descriptor owner for Package 6 source-only closure.",
    -1,
    p6_module_methods,
    NULL,
    NULL,
    NULL,
    NULL,
};

PyMODINIT_FUNC
PyInit__package6_fd_custody(void)
{
    PyObject *module;

    if (PyType_Ready(&P6FdOwnerType) < 0) {
        return NULL;
    }

    module = PyModule_Create(&p6_module);
    if (module == NULL) {
        return NULL;
    }
    Py_INCREF(&P6FdOwnerType);
    if (PyModule_AddObject(module, "FdOwner", (PyObject *)&P6FdOwnerType) != 0) {
        Py_DECREF(&P6FdOwnerType);
        Py_DECREF(module);
        return NULL;
    }
    if (PyModule_AddStringConstant(module, "OWNERSHIP_MODEL", "NATIVE_OBJECT_V1") != 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
