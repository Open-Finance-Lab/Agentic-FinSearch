Chrome Web Store (No Local Install)
====================================

The fastest way to start using Agentic FinSearch. Add the published
extension from the Chrome Web Store, open a supported financial site, and
the agent appears as a floating window — **no local backend, no Docker,
no command line required**.

.. admonition:: When to choose this path
   :class: note

   Pick this path if you just want to *use* the agent. If you intend to
   modify the code, run a custom backend, or develop new features, see
   :doc:`install_agent_with_installer` (Docker) or :doc:`manual_install`
   (``uv``) instead.

Prerequisites
-------------

* `Google Chrome <https://www.google.com/chrome/>`_ (Brave is currently
  unsupported).

Steps
-----

1. **Install the extension.** Open the Chrome Web Store listing and click
   *Add to Chrome*:

   `Agentic FinSearch on the Chrome Web Store
   <https://chromewebstore.google.com/detail/agentic-finsearch/aehnlpneoncdfioafiigiljmbghccami?hl=en&authuser=0>`_

2. **Open a supported site**, for example `Yahoo Finance
   <https://finance.yahoo.com/>`_. Agentic FinSearch will pop up as a
   floating window over the page.

3. **Start chatting.** Type a question into the chat box — for example,
   *"Which two days in September 2025 had the highest and lowest
   closing prices for Nvidia?"* — and the agent will respond with
   sourced answers drawn from the Yahoo Finance page you are on. See
   :doc:`../usage/basic_usage` for a tour of modes, settings, and the
   Validate button.

Troubleshooting
---------------

* **The floating window doesn't appear.** Confirm you are on a supported
  financial site (Yahoo Finance is the canonical test page). Reload the
  tab after installing the extension.
* **The extension is hidden.** Click the puzzle-piece icon in Chrome's
  toolbar and pin *Agentic FinSearch* so it stays visible.
* **You see a different browser warning.** The extension is only
  published for Google Chrome. Chromium-derived browsers may work but
  are not officially supported; Brave is known to be incompatible.
