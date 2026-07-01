The backend is designed to run via Docker:

.. code-block:: bash

   docker compose up

Use the manual steps below only if you need to work outside Docker (for example when iterating quickly on Django views).

Prerequisites
-------------

* Python 3.12 (``uv`` downloads it automatically if missing)
* ``uv`` (https://github.com/astral-sh/uv)
* Node.js 18 (for rebuilding the browser extension)

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

Environment Variables
---------------------

Copy ``Main/backend/.env.example`` to ``Main/backend/.env`` and add the required API keys before running either Docker or ``uv run`` commands.
