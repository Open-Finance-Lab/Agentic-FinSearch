Dev Case Studies
================

This section documents real-world development challenges, debugging processes, and their resolutions within the Agentic FinSearch project. These case studies serve as a knowledge base to prevent recurring issues and improve system robustness.

.. contents:: Table of Contents
   :depth: 2
   :local:

Case Study Template
-------------------

*Use the following structure for new case studies:*

.. code-block:: rst

   [Case Study Title] (MM/DD/YYYY)
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

   **Problem**
   [Describe the issue, symptoms, and impact.]

   **Debugging Process**
   [Outline the steps taken to identify the root cause, tools used, and key findings.]

   **Resolution**
   [Detail the fix implemented, configuration changes, and long-term prevention measures.]

---

Fedora Droplet Storage Exhaustion (01/07/2026)
----------------------------------------------

**Problem**

The Fedora droplet ran out of storage on the Btrfs partition shared by ``/``, ``/var``, and ``/home``. This caused the auto-deployment via GitHub workflow to fail. The root cause was identified as a buildup of old Podman images, specifically redundant Playwright browser layers (~1GB each), and uncapped system logs.

**Debugging Process**

1.  **Disk vs. Inodes**: Verified partition status using ``df -h`` and ``df -i``. The lack of inodes indicated a Btrfs filesystem.

2.  **Space Analysis**: Performed deep scans using ``du -sh`` and ``du -ahx``. Discovered that ``/home/deploy`` was consuming over 37GB, primarily in Podman's overlay storage.

3.  **Deadlock Identification**: Standard ``podman prune`` commands failed because the filesystem was too full to perform the rename operations required for metadata updates.

**Resolution**

1.  **Immediate Cleanup**: Vacuumed journal logs (``journalctl --vacuum-time=7d``) and manually deleted the Podman storage directory (``rm -rf ~/.local/share/containers/storage``) as root user to recover 19GB of space. Remember to switch to root user when running commands like ``rm -rf``!
2.  **Long-term Prevention**:

    -   **Workflow**: Added ``podman image prune -f`` to the ``backend-deploy.yml`` workflow.

    -   **Dockerfile Optimization**: Pinned ``PLAYWRIGHT_BROWSERS_PATH`` and disabled redundant browser downloads.

    -   **Log Management**: Capped the system journal to 500MB via a drop-in configuration in ``/etc/systemd/journald.conf.d/limit-size.conf``.
