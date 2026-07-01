Start the Agent on macOS
=========================

Quick Start (Recommended)
-------------------------

#. Open Terminal and move to the project root.
#. Copy ``Main/backend/.env.example`` to ``Main/backend/.env`` and add your API keys.
#. Run ``docker compose up``.

Docker builds the backend image with ``uv`` and exposes it on port ``8000``. The browser extension can then connect to ``http://localhost:8000``.

Manual Run with uv
------------------

Only needed if you prefer to run the Django server directly.

.. code-block:: bash

   cd Main/backend
   uv sync --python 3.12 --frozen
   uv run playwright install chromium
   uv run python manage.py runserver

Frontend Checks
---------------

If you change frontend code rebuild the bundle:

.. code-block:: bash

   cd Main/frontend
   bun install
   bun run build:full

Load the extension from ``Main/frontend/dist`` via **chrome://extensions** (or the equivalent in your Chromium-based browser).
