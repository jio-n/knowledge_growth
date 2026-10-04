"""Windows ownership of the process tree (KILL_ON_JOB_CLOSE).

Loaded only on Windows. Failing to establish ownership fails process startup.
"""
import ctypes
from ctypes import wintypes


class WindowsJob:
    def __init__(self, process):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_processes', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(field, ctypes.c_uint64) for field in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                        ('peak_job_memory', ctypes.c_size_t)]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle: raise OSError('job_create_failed')
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle)))):
            self.close()
            raise OSError('job_assign_failed')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
