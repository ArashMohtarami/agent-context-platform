import csv
import io


def execute(store, c):
    if c["action"] != "labels":
        raise ValueError("action")
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["recipient", "address"])
    for row in c["rows"]:
        out.write(row["recipient"] + "," + row["address"] + "\n")
    return {"csv": out.getvalue()}
