Quick Install with Docker
==========================

Docker provides the simplest path to a working backend.

Prerequisites
-------------

* Docker Desktop (Windows/macOS) or Docker Engine (Linux)
* At least one API key (OpenAI, Anthropic, or DeepSeek)

Steps
-----

1. Clone the repository and move into the project directory.

   .. code-block:: bash

      git clone https://github.com/Open-Finance-Lab/Agentic-FinSearch.git
      cd Agentic-FinSearch

2. Copy the environment template and add your keys.

   .. code-block:: bash

      cp Main/backend/.env.example Main/backend/.env

   Edit ``.env`` and set at least one of ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, or ``DEEPSEEK_API_KEY``.

3. Build and run the backend.

   .. code-block:: bash

      docker compose up

   The first run builds the backend image using ``uv``. Subsequent runs reuse the cached layers.

4. Load the browser extension from ``Main/frontend/dist`` via the extensions page of your Chromium-based browser.

Updating the Image
------------------

Rebuild whenever dependencies change:

.. code-block:: bash

   docker compose build --no-cache
   docker compose up --force-recreate
