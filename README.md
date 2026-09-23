# printed
An automated, background email-to-thermal-printer daemon for e-commerce resellers.   It polls an IMAP inbox for incoming label notifications from **Vinted**, **Poshmark**, and **eBay**, silently sends the labels to a 4x6 thermal printer, custom packing slips with item checklists, order metadata, and thank-you notes.

I used AI to write this program so I no longer had to manually crop pdf labels that vinted would send. The packing slip helps with knowing which label is what when they come out of your printer.

Features
Automated Polling: Monitors your inbox in the background without user intervention.

Smart Cropping: Automatically crops margins off uncropped 8.5x11 labels (e.g., Vinted) to fit standard 4x6 thermal labels.

Companion Packing Slips: Prints a matching 4x6 packing checklist slip right after each shipping label:

Vinted: Crops and stamps the recipient address block, lists items, package size, and tracking numbers.

Poshmark: Parses item descriptions and buyer @handle.

eBay: Extracts item titles, order numbers, quantities, and buyer usernames.

Silent Printing: Integrates with SumatraPDF for background printing with auto-fit margins.

Automated Retention: Automatically deletes processed notification emails older than a configurable retention window (e.g., 7 days).

External Configuration: Control all paths, printer targets, coordinates, and toggles via an external config.ini without recompiling.

Prerequisites
Windows 10 or 11

SumatraPDF installed (used for silent, command-line PDF spooling).

Download: SumatraPDF

Default install path: C:\Users\\AppData\Local\SumatraPDF\SumatraPDF.exe

4x6 Thermal Label Printer (e.g., KNAON, Rollo, Munbyn, Zebra) set up and visible in Windows Printers & Scanners.

Email Account Setup (Gmail Example)
Because modern email providers require 2-Factor Authentication (2FA), standard passwords will not work over IMAP. You must generate a dedicated App Password.

1. Generate a Google App Password
Go to your Google Account Security Settings.

Ensure 2-Step Verification is turned ON.

In the search bar at the top of the Google Account page, search for App passwords.

Give your app a name (e.g., ThermalPrintDaemon) and click Create.

Copy the generated 16-character password (e.g., abcd efgh ijkl mnop). This is the password you will use in config.ini (spaces can be removed).

2. Verify IMAP Access
In Gmail, click the Settings gear → See all settings.
Navigate to the Forwarding and POP/IMAP tab.
Ensure Status: IMAP is enabled is selected.

Installation & Configuration
Option A: Running from Source
Clone this repository:

Bash
git clone [https://github.com/yourusername/ThermalPrintDaemon.git](https://github.com/rgburg3rs/printed.git)
cd printed
Install Python dependencies:

Bash
pip install pypdf reportlab
Configure your settings:

Update your printer name, email credentials, and SumatraPDF path.

Run the daemon:

Bash
python email_print_daemon.py
# Or run completely silently in the background:
pythonw email_print_daemon.py
