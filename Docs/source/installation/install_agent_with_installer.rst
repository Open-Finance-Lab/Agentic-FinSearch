Quick Install with Docker
==========================

Docker provides the simplest path to a working backend.

Prerequisites
-------------

* Docker Desktop (Windows/macOS) or Docker Engine (Linux)
* An **OpenAI API key** — required by default: the container exits at startup without one (override with ``REQUIRE_OPENAI_API_KEY=0``, not recommended)
* A **Google API key** (``GOOGLE_API_KEY``) for the default ``FinGPT`` (Gemini) model; Anthropic/DeepSeek keys only if you use their models

Steps
-----

1. Clone the repository and move into the project directory.

   .. code-block:: bash

      git clone https://github.com/Open-Finance-Lab/Agentic-FinSearch.git
      cd Agentic-FinSearch

2. Copy the environment template and add your keys.

   .. code-block:: bash

      cp Main/backend/.env.example Main/backend/.env

   Edit ``.env`` and set ``OPENAI_API_KEY`` (required by default — the container refuses to start without it) and ``GOOGLE_API_KEY`` (used by the default ``FinGPT`` model). To run without an OpenAI key, set ``REQUIRE_OPENAI_API_KEY=0`` (not recommended).

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
