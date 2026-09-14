"""Smart Shutdown Hub - Omniscient Edition.

Heuristic shutdown manager and countdown timer for low-power machines.

Package layout:
    sshub.config         - Application constants and thresholds.
    sshub.events         - Thread-safe event bus (queue.Queue based).
    sshub.platform_layer - native Windows platform integration.
    sshub.sensors        - Daemon-thread sensor implementations.
    sshub.core           - Monitoring engine, profiles CRUD, action executor.
    sshub.gui            - CustomTkinter UI (main window + emergency overlay).
"""
