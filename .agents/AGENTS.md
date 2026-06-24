# Project Rules & Guidelines

## Python Environment Management with Rye
If `rye` is available on the system:
1. Always manage virtual environments, python toolchains, and package installations using Rye.
2. To set up a virtual environment in a project that does not yet have Rye configurations:
   - Run `rye init --virtual` in the project root.
   - Pin the required Python version with `rye pin <python_version>`.
   - Remove any pre-existing broken virtual environments using `rm -rf .venv`.
   - Add packages using `rye add <package_name>`.
   - Re-sync and build the virtual environment using `rye sync`.
3. Avoid executing raw `.venv/bin/pip install` if it would bypass Rye's lock files or environment syncs.
