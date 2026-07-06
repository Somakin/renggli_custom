import io
import re

import frappe
import qrcode
from frappe.utils.file_manager import save_file
from frappe.utils.pdf import get_pdf
from pypdf import PdfReader, PdfWriter


def generate_qr_code(doc, method=None):
    if not doc.company_address or not doc.customer_address:
        return

    company = frappe.get_doc("Company", doc.company)
    if not company.default_bank_account:
        return

    bank = frappe.get_doc("Account", company.default_bank_account)
    if not bank.iban:
        return

    cmp_addr = frappe.get_doc("Address", doc.company_address)
    pay_addr = frappe.get_doc("Address", doc.customer_address)

    cmp_country = frappe.get_doc("Country", cmp_addr.country)
    pay_country = frappe.get_doc("Country", pay_addr.country)

    creditor_street, creditor_number = split_street(cmp_addr.address_line1)
    debtor_street, debtor_number = split_street(pay_addr.address_line1)

    payload = build_swiss_qr_payload(
        iban=(bank.iban or "").replace(" ", ""),
        creditor_name=doc.company or "",
        creditor_street=creditor_street,
        creditor_number=creditor_number,
        creditor_pincode=cmp_addr.pincode or "",
        creditor_city=cmp_addr.city or "",
        creditor_country=(cmp_country.code or "").upper(),
        amount=doc.grand_total or 0,
        currency=doc.currency or "CHF",
        debtor_name=doc.customer or "",
        debtor_street=debtor_street,
        debtor_number=debtor_number,
        debtor_pincode=pay_addr.pincode or "",
        debtor_city=pay_addr.city or "",
        debtor_country=(pay_country.code or "").upper(),
        message=doc.name or "",
    )

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    safe_name = doc.name.replace(" ", "_").replace("/", "_")
    file_name = f"qr_code_{safe_name}.png"

    old_files = frappe.get_all(
        "File",
        filters={
            "file_name": ["like", f"qr_code_{safe_name}%"],
        },
        pluck="name",
    )

    for file_id in old_files:
        frappe.delete_doc("File", file_id, ignore_permissions=True)

    file_doc = save_file(
        file_name,
        buffer.getvalue(),
        None,
        None,
        is_private=0,
        decode=False,
    )

    frappe.db.set_value(
        "File",
        file_doc.name,
        {
            "attached_to_doctype": None,
            "attached_to_name": None,
            "attached_to_field": None,
        },
        update_modified=False,
    )

    frappe.db.set_value(
        "Sales Invoice",
        doc.name,
        "custom_qr_code_image",
        file_doc.file_url,
        update_modified=False,
    )

def build_swiss_qr_payload(
    iban,
    creditor_name,
    creditor_street,
    creditor_number,
    creditor_pincode,
    creditor_city,
    creditor_country,
    amount,
    currency,
    debtor_name,
    debtor_street,
    debtor_number,
    debtor_pincode,
    debtor_city,
    debtor_country,
    message,
):

   
    return "\n".join([
        "SPC",
        "0200",
        "1",
        iban,
        "S",
        creditor_name,
        creditor_street,
        creditor_number,
        creditor_pincode,
        creditor_city,
        creditor_country,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        f"{amount:.2f}",
        currency,
        "S",
        debtor_name,
        debtor_street,
        debtor_number,
        debtor_pincode,
        debtor_city,
        debtor_country,
        "NON",
        "",
        message,
        "EPD",
        "",
        "",
    ])

@frappe.whitelist()
def download_invoice_with_qr(name):
    pdf_bytes = get_invoice_with_qr_pdf(name)

    frappe.local.response.filename = f"{name}_mit_qr.pdf"
    frappe.local.response.filecontent = pdf_bytes
    frappe.local.response.type = "download"


def get_invoice_with_qr_pdf(name):
    doc = frappe.get_doc("Sales Invoice", name)

    if not doc.custom_qr_code_image:
        generate_qr_code(doc)
        frappe.db.commit()
        doc.reload()

    invoice_pdf = frappe.get_print(
        "Sales Invoice",
        name,
        print_format="Rechnung klein",
        as_pdf=True,
        no_letterhead=0,
    )

    qr_pdf = frappe.get_print(
        "Sales Invoice",
        name,
        print_format="QR Zahlteil",
        as_pdf=True,
        no_letterhead=0,
    )

    writer = PdfWriter()

    for pdf_bytes in [invoice_pdf, qr_pdf]:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
          writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)        

    return output.getvalue()


def download_and_attach_invoice_with_qr(docname):
    pdf_bytes = get_invoice_with_qr_pdf(docname)
    file_name = f"{docname}_mit_qr.pdf"

    existing = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Sales Invoice",
            "attached_to_name": docname,
            "file_name": file_name,
        },
        pluck="name",
    )

    for file_id in existing:
        frappe.delete_doc("File", file_id, ignore_permissions=True)

    return save_file(
        file_name,
        pdf_bytes,
        "Sales Invoice",
        docname,
        is_private=0,
        decode=False,
    )

def split_street(address):
    if not address:
        return "", ""

    match = re.match(r"^(.*?)(\d+\w*)$", address.strip())

    if match:
        return match.group(1).strip(), match.group(2).strip()

    return address, ""

def attach_invoice_with_qr_on_submit(doc, method=None):
    download_and_attach_invoice_with_qr(doc.name)

def get_qr_payment_part_html(doc):
    company = frappe.get_doc("Company", doc.company)
    bank = frappe.get_doc("Account", company.default_bank_account) if company.default_bank_account else None
    iban = bank.iban if bank and bank.iban else ""

    cmp_addr = frappe.get_doc("Address", doc.company_address) if doc.company_address else None
    pay_addr = frappe.get_doc("Address", doc.customer_address) if doc.customer_address else None

    amount = "{:,.2f}".format(doc.grand_total or 0).replace(",", " ")
    qr_url = frappe.utils.get_url(doc.custom_qr_code_image)

    return frappe.render_template("""
<div style="width:210mm;height:105mm;font-family:Arial,sans-serif;font-size:8pt;color:#000;position:relative;">

  <div style="position:absolute;left:0;top:0;width:62mm;height:105mm;padding:5mm;box-sizing:border-box;">
    <div style="font-weight:bold;font-size:11pt;">Empfangsschein</div>

    <div style="margin-top:4mm;">
      <strong>Konto / Zahlbar an</strong><br>
      {{ iban }}<br>
      {{ doc.company }}<br>
      {% if cmp_addr %}
        {{ cmp_addr.address_line1 }}<br>
        {{ cmp_addr.pincode }} {{ cmp_addr.city }}
      {% endif %}
    </div>

    <div style="margin-top:8mm;">
      <strong>Zahlbar durch</strong><br>
      {{ doc.customer }}<br>
      {% if pay_addr %}
        {{ pay_addr.address_line1 }}<br>
        {{ pay_addr.pincode }} {{ pay_addr.city }}
      {% endif %}
    </div>

    <div style="position:absolute;left:5mm;bottom:18mm;">
      <table style="border-collapse:collapse;">
        <tr>
          <td><strong>Währung</strong></td>
          <td style="padding-left:8mm;"><strong>Betrag</strong></td>
        </tr>
        <tr>
          <td>{{ doc.currency }}</td>
          <td style="padding-left:8mm;">{{ amount }}</td>
        </tr>
      </table>
    </div>

    <div style="position:absolute;right:5mm;bottom:7mm;font-size:7pt;font-weight:bold;">
      Annahmestelle
    </div>
  </div>

  <div style="position:absolute;left:62mm;top:0;width:62mm;height:105mm;padding:5mm;box-sizing:border-box;">
    <div style="font-weight:bold;font-size:11pt;">Zahlteil</div>

    <img src="{{ qr_url }}" style="width:46mm;height:46mm;margin-top:8mm;">

    <div style="margin-top:5mm;">
      <table style="border-collapse:collapse;">
        <tr>
          <td><strong>Währung</strong></td>
          <td style="padding-left:8mm;"><strong>Betrag</strong></td>
        </tr>
        <tr>
          <td>{{ doc.currency }}</td>
          <td style="padding-left:8mm;">{{ amount }}</td>
        </tr>
      </table>
    </div>
  </div>

  <div style="position:absolute;left:124mm;top:0;width:86mm;height:105mm;padding:5mm;box-sizing:border-box;">
    <strong>Konto / Zahlbar an</strong><br>
    {{ iban }}<br>
    {{ doc.company }}<br>
    {% if cmp_addr %}
      {{ cmp_addr.address_line1 }}<br>
      {{ cmp_addr.pincode }} {{ cmp_addr.city }}
    {% endif %}

    <div style="margin-top:8mm;">
      <strong>Zusätzliche Informationen</strong><br>
      {{ doc.name }}
    </div>

    <div style="margin-top:8mm;">
      <strong>Zahlbar durch</strong><br>
      {{ doc.customer }}<br>
      {% if pay_addr %}
        {{ pay_addr.address_line1 }}<br>
        {{ pay_addr.pincode }} {{ pay_addr.city }}
      {% endif %}
    </div>
  </div>
</div>
""", {
        "doc": doc,
        "iban": iban,
        "cmp_addr": cmp_addr,
        "pay_addr": pay_addr,
        "amount": amount,
        "qr_url": qr_url,
    })
