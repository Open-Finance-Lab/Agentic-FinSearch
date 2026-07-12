The backend is designed to run via Docker:

.. code-block:: bash

   docker compose up

Use the manual steps below only if you need to work outside Docker (for example when iterating quickly on Django views).

Prerequisites
-------------

* Python 3.12 (``uv`` downloads it automatically if missing)
* ``uv`` (https://github.com/astral-sh/uv)
* Bun (https://bun.sh) — installs dependencies and runs the browser-extension build

Install Dependencies with uv
----------------------------

.. code-block:: bash

   cd Main/backend
   uv sync --python 3.12 --frozen
   uv run playwright install chromium

``uv`` now creates ``.venv`` inside ``Main/backend``. Activating it is optional because ``uv run`` automatically uses the environment.

Run the Server
--------------

.. code-block:: bash

   cd Main/backend
   uv run python manage.py runserver

Frontend Build (optional)
-------------------------

Only needed when you change the extension source.

.. code-block:: bash

   cd Main/frontend
   bun install
   bun run build:full

.. note::
   The commands above produce a **keyless** dev build: it sends no
   ``Authorization`` header and is meant for a dev backend running with auth
   open. To build a release bundle that talks to a **key-gated** backend, bake
   the coarse-gate key in at build time:

   .. code-block:: bash

      cd Main/frontend
      FINGPT_API_KEY=YOUR_BACKEND_KEY bun run build:full

   A webpack ``DefinePlugin`` bakes this *frontend* build-time key into the
   (extractable) bundle as a coarse gate against drive-by abuse. It is distinct
   from — though normally equal to — the backend ``FINGPT_API_KEY`` env var
   described below.

Environment Variables
---------------------

Copy ``Main/backend/.env.example`` to ``Main/backend/.env`` and add the required API keys before running either Docker or ``uv run`` commands.
