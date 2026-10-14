"""Safe text serialization for spreadsheet-readable CSV exports."""


def safe_csv_cell(value):
    """Prevent untrusted text from being interpreted as a spreadsheet formula."""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text
