
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

- **Pin-to-Place Button**: Pins the pop up to its current location. The pop up will not move when the user scrolls the
  page.

- **Setting Button**: Opens the settings page, and may be closed by clicking anywhere outside the settings page but
  inside the pop up. It allows users to choose foundation models and set preferred links for Advanced search.

Main Body
~~~~~~~~~
This part shows the current conversation between the user and the search agent. The agent uses session-based memory to maintain conversation context.

Prompt Box
~~~~~~~~~~

User may type their prompts inside the prompt box and choose either of the two Ask buttons to send the prompt.
- **Ask Button**: The search agent only incorporates context scraped from the current domain, including the currently active page.

- **Advanced Ask Button**: The search agent incorporates context from the current web page as well as any successfully
  scraped user-defined links (Preferred links) and sources returned by the open-domain search. User can monitor the
  searching and scraping logs in real-time through the Terminal/PowerShell window running its back-end.

Two more buttons appear above the prompt box and below where conversations are shown.
- **Clear Button**: Clears the currently shown conversations.

- **Source Button**: Shows the sources used by the search agent to answer the user's prompt. The sources are shown in a
  pop up and may be closed.'

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
