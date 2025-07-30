import smtplib
from email.mime.text import MIMEText
from datetime import datetime

# Dummy scraped data for now
scraped_data = """
🇬🇧 UK: Company A -4.5% | €10B
🇩🇪 Germany: Company B -3.8% | €50B
🇺🇸 USA: Company C -4.1% | $80B
"""

# Create the email message
msg = MIMEText(f"Daily Stock Losers Report - {datetime.now().strftime('%Y-%m-%d')}\n\n" + scraped_data)
msg["Subject"] = "📉 Daily Stock Losers Report"
msg["From"] = "narmeenyousaf098@gmail.com"
msg["To"] = "eneljarving@gmail.com"

# Send the email using SMTP (Gmail)
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login("narmeenyousaf098@gmail.com", "djbbywapeziqfqrw")
    server.send_message(msg)
