
.. admonition:: When to use this section
   :class: note

   Please make sure the search agent is successfully installed
   and running before testing out any of the examples below!

.. note::
   As a reminder, the search agent currently does NOT work on **Brave** browser.


User Interface
--------------

The search agent's user interface (UI) automatically pops up when user loads any supported website. We generally call
this UI the "pop up". It appears toward the left of the screen and may be *dragged* by holding the top bar or
*resized* by dragging the bottom right corner.

When the UI first pops up, it auto-scrapes the currently active page (the webpage it is launched on). This usually takes a couple seconds and is shown via a loading message inside the agent's top bar.

Top Bar
~~~~~~~

The top bar of the pop up contains the following elements:
- **Close Button**: Closes the pop up.
- **Minimize Button**: Minimizes the pop up at its current location.

- **Position-Mode Button**: Toggles how the pop up behaves when you scroll.
  **Hover in Place** keeps the pop up fixed on screen while the page scrolls;
  **Move with Page** lets it scroll with the page content. The button label
  shows the currently active mode.

- **Setting Button**: Opens the settings page, and may be closed by clicking anywhere outside the settings page but
  inside the pop up. It allows users to choose foundation models and set preferred links for Research mode.

Main Body
~~~~~~~~~
This part shows the current conversation between the user and the search agent. The agent uses session-based memory to maintain conversation context.

Prompt Box
~~~~~~~~~~

Type your prompt inside the prompt box and press **Enter** to send it. A
**mode selector** next to the prompt box chooses how the agent answers:

- **Thinking** (default): the agent works from the context scraped from the
  current page and calls MCP tools (SEC-EDGAR, Yahoo Finance, TradingView,
  XBRL taxonomy) when it needs live financial data.

- **Research**: the agent runs the deep-research pipeline — it decomposes the
  question, searches the open web plus your Preferred links in parallel, and
  synthesizes a sourced answer.

More buttons appear above the prompt box and below where conversations are shown.

- **Clear Button**: Clears the currently shown conversations.

- **Source Button**: Shows the sources used by the search agent to answer the
  user's prompt. The sources are shown in a pop up and may be closed.

- **Validate Button**: Appears in a response's toolbar when the response
  contains numerical claims the XBRL pipeline can check. Click it to verify
  each claim against SEC XBRL filings — see :doc:`../xbrl_validation`.

These components make up the current Agentic FinSearch demo. The documentation will be updated regularly to keep up
with latest progress.

Supported Websites
------------------

Agentic FinSearch automatically activates on the following financial websites:

* **Bloomberg**: ``https://www.bloomberg.com/*``
* **Yahoo Finance**: ``https://finance.yahoo.com/*``
* **CDM/FINOS**: ``https://cdm.finos.org/*`` and ``https://www.finos.org/*``
* **MathCup**: ``https://mathcup.com/*``
* **CNBC**: ``https://www.cnbc.com/*``
* **TradingView**: ``https://www.tradingview.com/*``
* **XYZ Terminal**: ``https://xyzterminal.com/*`` and ``https://app.xyzterminal.com/*``
* **Kalshi**: ``https://kalshi.com/*``
* **Polymarket**: ``https://polymarket.com/*``

When you navigate to any of these websites, the Agentic FinSearch popup will automatically appear, ready for your financial queries.

.. tip::
   If the popup doesn't appear on a supported site:
   
   1. Refresh the page
   2. Check that the extension is enabled in your browser
   3. Ensure the backend server is running
