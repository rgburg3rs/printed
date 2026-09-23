# printed

An automated, background email-to-thermal-printer daemon for e-commerce resellers. It polls an IMAP inbox for incoming shipping label notifications from Vinted, Poshmark, and eBay, silently prints them to a 4x6 thermal printer via SumatraPDF, and generates custom companion packing slips with item checklists, buyer/order metadata, and a large decorative Thank You block.

> Why this was built:
> I used AI to build this tool so I no longer had to manually crop 8.5x11 PDF labels sent by Vinted. The companion packing slip solves the messy problem of labels piling up out of the printer by printing a matching checklist directly behind each label so you always know what goes in which box.

---

Features:
- Automated Polling: Monitors your IMAP inbox in the background without manual intervention.
- Filtering on sender addresses: Personal use case is that I have a dedicated mailbox and only a few senders are considered for printing labels.
- Smart Cropping: Automatically strips outer margins from 8.5x11 PDF labels (Vinted) to cleanly fit standard 4x6 thermal label stock.
- Companion Packing Slips: Immediately spools a matching 4x6 checklist right behind each label:
  * Vinted: Crops and stamps the recipient address block, lists items with check boxes, package size, and tracking numbers.
  * Poshmark: Parses item titles and the buyer\'s @handle directly from cleaned subject headers.
  * eBay: Extracts item titles, order numbers, quantities, and buyer handles with built-in noise and URL filtering.
- Large Floral Thank You Banner: Generates a prominent, vector-drawn floral Thank You card occupying the lower 1/3rd of every packing slip.
- Silent Printing: Integrates with SumatraPDF for silent, command-line spooling with automatic shrink-to-fit scaling.
- Automated Retention: Cleans up your inbox by permanently purging processed notification emails older than a configurable window (e.g., 3-7 days).

---

Prerequisites:
- OS: Windows 10 or 11
- Printer: 4x6 Thermal Label Printer (e.g., KNAON, Rollo, Munbyn, Zebra) set up and visible in Windows Printers & Scanners.
- PDF Engine: SumatraPDF (used for silent CLI printing).
  Default path: C:\Users\administrator\AppData\Local\SumatraPDF\SumatraPDF.exe
- Python: Python 3.10+

---

Email Account Setup (Gmail Example):

Modern email providers block raw account passwords over IMAP. You must generate a dedicated App Password.

1. Generate a Google App Password:
   - Go to your Google Account Security Settings (https://myaccount.google.com/security).
   - Confirm 2-Step Verification is turned ON.
   - In the search bar at the top of the Google Account page, search for App passwords.
   - Set an app name (e.g., printed) and select Create.
   - Copy the generated 16-character password (e.g., abcd efgh ijkl mnop). Use this in your script configuration (spaces are optional).

2. Verify IMAP Access:
   - In Gmail, open the Settings gear -> See all settings.
   - Click the Forwarding and POP/IMAP tab.
   - Ensure Status: IMAP is enabled is selected, then save changes.

---

Installation & Setup:

1. Clone this repository:
   git clone https://github.com/rgburg3rs/printed.git
   cd printed

2. Install Python dependencies:
   pip install pypdf reportlab

3. Configure the script:
   Open email_print_daemon.py and adjust the configuration variables to match your environment:
   - IMAP_SERVER = "imap.gmail.com"
   - IMAP_PORT = 993
   - EMAIL_ACCOUNT = "your_email@gmail.com"
   - EMAIL_PASSWORD = "your_16_char_app_password"
   - PRINTER_NAME = "LABEL_Printer"
   - SUMATRA_PATH = r"C:\Users\administrator\AppData\Local\SumatraPDF\SumatraPDF.exe"
   - CROP_TOP = 90
   - CROP_BOTTOM = 85
   - CROP_LEFT = 37
   - CROP_RIGHT = 473
   - RETENTION_DAYS = 3

4. Run the daemon:
   - Run in terminal (shows live console log output):
     python email_print_daemon.py
   - Run silently in background (no console window):
     pythonw email_print_daemon.py

---

Run on Windows Startup:

To launch the daemon automatically whenever you sign into Windows:
1. Press Win + R, type shell:startup, and hit Enter.
2. Right-click inside the folder -> New -> Shortcut.
3. Set the target to:
   pythonw.exe "C:\Repo\VintedPrinted\email_print_daemon.py"
4. Click Next, name the shortcut printed, and click Finish.

Logs are automatically written to logs/label_printer.log with daily midnight rotations.
