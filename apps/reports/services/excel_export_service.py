import io
import xlsxwriter
from datetime import datetime


class ExcelExportService:

    @staticmethod
    def generate_report(data: dict):

        output = io.BytesIO()

        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # 🎨 Styles
        header_format = workbook.add_format({
            'bold': True,
            'border': 1,
            'align': 'center'
        })

        money_format = workbook.add_format({'num_format': '#,##0'})

        # =========================
        # 1. SUMMARY SHEET
        # =========================
        sheet = workbook.add_worksheet("Umumiy")

        summary = data["summary"]

        sheet.write("A1", "Umumiy hisobot", header_format)

        rows = [
            ("Jami daromad", summary["totalRevenue"]),
            ("Sof foyda", summary["totalProfit"]),
            ("Xarajat", summary["totalExpenses"]),
            ("Buyurtmalar soni", summary["totalOrders"]),
            ("O'rtacha chek", summary["averageOrderValue"]),
            ("Mijozlar soni", summary["totalCustomers"]),
        ]

        for i, (label, value) in enumerate(rows, start=2):
            sheet.write(f"A{i}", label)
            sheet.write(f"B{i}", value, money_format)

        # =========================
        # 2. BRANCH STATS
        # =========================
        sheet2 = workbook.add_worksheet("Filiallar")

        headers = ["Filial", "Daromad", "Buyurtmalar", "Mijozlar"]

        for col, h in enumerate(headers):
            sheet2.write(0, col, h, header_format)

        for row, b in enumerate(data["branchStatistics"], start=1):
            sheet2.write(row, 0, b["store__name"])
            sheet2.write(row, 1, b["revenue"], money_format)
            sheet2.write(row, 2, b["orders"])
            sheet2.write(row, 3, b["customers"])

        # =========================
        # 3. TOP PRODUCTS
        # =========================
        sheet3 = workbook.add_worksheet("Top mahsulotlar")

        headers = ["#", "Mahsulot", "Sotilgan", "Daromad"]

        for col, h in enumerate(headers):
            sheet3.write(0, col, h, header_format)

        for row, p in enumerate(data["topSellingProducts"], start=1):
            sheet3.write(row, 0, p["rank"])
            sheet3.write(row, 1, p["name"])
            sheet3.write(row, 2, p["totalSold"])
            sheet3.write(row, 3, p["totalRevenue"], money_format)

        # =========================
        # 4. CUSTOMER DEBT
        # =========================
        sheet4 = workbook.add_worksheet("Mijoz qarzlari")

        sheet4.write(0, 0, "Mijoz", header_format)
        sheet4.write(0, 1, "Qarz", header_format)

        for row, d in enumerate(data["debts"]["customerDebts"], start=1):
            sheet4.write(row, 0, d["customerName"])
            sheet4.write(row, 1, d["debt"], money_format)

        # =========================
        # 5. SUPPLIER DEBT
        # =========================
        sheet5 = workbook.add_worksheet("Taminotchi qarzlari")

        sheet5.write(0, 0, "Taminotchi", header_format)
        sheet5.write(0, 1, "Qarz", header_format)

        for row, d in enumerate(data["debts"]["supplierDebts"], start=1):
            sheet5.write(row, 0, d["supplierName"])
            sheet5.write(row, 1, d["debt"], money_format)

        workbook.close()
        output.seek(0)

        return output


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Izoh: ✅ YAXSHI — bu servis DB'ga tegmaydi, faqat OLDINDAN AGGREGATSIYA qilingan `data` dict'ini
#   (summary, filial statistikasi, top mahsulotlar, qarzlar) xlsx'ga yozadi. Bu ma'lumotlar CHEGARALANGAN
#   (65k xom sotuv qatori EMAS), shu sabab `in_memory=True` BytesIO xotira uchun xavfsiz.
#   ⚠️ ESLATMA: agar kelajakda bu yerga xom qatorlar (masalan har bir chek) qo'shilsa,
#   butun workbook RAMda yig'ilgani sabab xotira bosimi paydo bo'ladi — u holda streaming/xlsxwriter
#   `constant_memory=True` rejimiga o'tish kerak. Og'irlik data'ni yig'uvchi report_service'da (alohida auditlangan).
# Prioritet bo'yicha birinchi hal qilinishi kerak: [—]
# ═══════════════════════════════
