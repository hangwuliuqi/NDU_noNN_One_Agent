"""Compatibility entry point for the bundled fixed-parameter HFSS runner.

The original standalone script duplicated geometry, material, port, setup, and
sweep constants. Keeping two copies allowed them to drift apart. The single
implementation now lives in ``llmo.hfss_runner``; this wrapper preserves the
historical command name without maintaining a second model definition.
"""

from llmo.hfss_runner import main


if __name__ == "__main__":
    main()
