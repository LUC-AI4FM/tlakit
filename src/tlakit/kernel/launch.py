"""Entry point named by the kernelspec."""
from ipykernel.kernelapp import IPKernelApp

from . import TlaKernel

if __name__ == "__main__":
    IPKernelApp.launch_instance(kernel_class=TlaKernel)
