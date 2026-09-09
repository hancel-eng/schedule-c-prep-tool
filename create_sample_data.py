import os
import pandas as pd
import json
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

def generate_samples():
    os.makedirs("sample_data", exist_ok=True)

    # 1. Create Sample Excel File
    df_excel = pd.DataFrame([
        {"Date": "2026-01-10", "Payee": "Staples Office Supplies", "Amount": 145.20, "Category": "Office Expenses"},
        {"Date": "2026-01-15", "Payee": "Google Ads", "Amount": 300.00, "Category": "Marketing"},
        {"Date": "2026-01-20", "Payee": "Starbucks Coffee", "Amount": 18.50, "Category": "Meals"},
        {"Date": "2026-01-25", "Payee": "Apple Store", "Amount": 2899.00, "Category": "Computer Hardware"}, # Fixed asset > $2500
        {"Date": "2026-02-05", "Payee": "Trader Joe's", "Amount": 112.40, "Category": "Groceries"}, # Personal
        {"Date": "2026-02-12", "Payee": "John Subcontractor", "Amount": 850.00, "Category": "Subcontractor Fee"}, # 1099 check
        {"Date": "2026-02-28", "Payee": "Transfer to Chase Savings", "Amount": 1500.00, "Category": "Transfer"}, # Non-P&L
        {"Date": "2026-03-01", "Payee": "Client Invoice Payment", "Amount": -4500.00, "Category": "Revenue Deposit"}
    ])
    df_excel.to_excel("sample_data/Client_Expenses_2026.xlsx", index=False)
    print("Created sample_data/Client_Expenses_2026.xlsx")

    # 2. Create Sample PDF Bank Statement
    pdf_path = "sample_data/Bank_Statement_Jan_2026.pdf"
    c = canvas.Canvas(pdf_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 750, "FIRST NATIONAL BANK - MONTHLY STATEMENT")
    c.setFont("Helvetica", 10)
    c.drawString(50, 735, "Account: #XXXX-9842 | Period: 01/01/2026 - 01/31/2026")
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, 700, "Date         Description                                Amount")
    c.setFont("Helvetica", 10)

    rows = [
        ("01/05/2026", "DEPOSIT - CLIENT PAYMENT INVOICE #101", "+$3,500.00"),
        ("01/08/2026", "CHEVRON GAS STATION FUEL", "-$45.20"),
        ("01/12/2026", "ZOOM COMMUNICATIONS MONTHLY", "-$14.99"),
        ("01/18/2026", "UBER EATS RESTAURANT", "-$32.40"),
        ("01/22/2026", "ONLINE TRANSFER TO SAVINGS XXX44", "-$1,000.00"),
        ("01/28/2026", "CREDIT CARD AUTOMATIC PAYMENT", "-$450.00")
    ]
    
    y = 680
    for r in rows:
        c.drawString(50, y, r[0])
        c.drawString(130, y, r[1])
        c.drawString(420, y, r[2])
        y -= 20

    c.save()
    print("Created sample_data/Bank_Statement_Jan_2026.pdf")

    # 3. Create Year End Totals JSON
    totals = {
        "Advertising & Marketing": 1200.0,
        "Car & Truck Expenses": 850.0,
        "Contract Labor Subcontractors": 4500.0,
        "Office Equipment Computer": 3100.0,
        "Business Insurance": 650.0,
        "Health Insurance": 2400.0, # Flagged personal health
        "Meals & Dining": 480.0
    }
    with open("sample_data/Year_End_Totals.json", "w") as f:
        json.dump(totals, f, indent=2)
    print("Created sample_data/Year_End_Totals.json")

if __name__ == "__main__":
    generate_samples()
