"""Generate small BIFF8 workbooks from synthetic, independently chosen values."""
import io
from dataclasses import dataclass


@dataclass(frozen=True)
class Cell:
    value: object
    number_format: str = "General"


@dataclass(frozen=True)
class Error:
    value: str = "#DIV/0!"


def workbook(sheets, *, datemode=0):
    # Import lazily so a missing optional test writer cannot mask the public
    # unsupported-XLS regression with a collection failure.
    import xlwt

    book = xlwt.Workbook()
    book.set_dates_1904(bool(datemode))
    styles = {}
    for name, rows in sheets:
        sheet = book.add_sheet(name)
        for row_index, row in enumerate(rows):
            for column_index, item in enumerate(row):
                cell = item if isinstance(item, Cell) else Cell(item)
                if cell.number_format not in styles:
                    styles[cell.number_format] = xlwt.easyxf(num_format_str=cell.number_format)
                style = styles[cell.number_format]
                if isinstance(cell.value, Error):
                    sheet.row(row_index).set_cell_error(column_index, cell.value.value, style)
                else:
                    sheet.write(row_index, column_index, cell.value, style)
    result = io.BytesIO()
    book.save(result)
    return result.getvalue()
