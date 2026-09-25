import pandas as pd
import psycopg2
import unicodedata
from rapidfuzz import fuzz, process


# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE = "2019-Update.xlsx"
OUTPUT_FILE = "output_refine.xlsx"

DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "dbname": "gaushala",
    "user": "postgres",
    "password": "postgres"
}

# Minimum fuzzy similarity required for a related-name match.
# You can increase this to 85/90 if you want stricter matching.
FUZZY_THRESHOLD = 75


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def clean_name(value):
    """
    Normalize a Gujarati/Unicode name for comparison.
    """
    if pd.isna(value):
        return ""

    value = str(value).strip()

    # Unicode normalization
    value = unicodedata.normalize("NFC", value)

    # Remove unnecessary spaces
    value = " ".join(value.split())

    return value


def clean_number(value):
    """
    Convert milk value to numeric.
    Empty/non-numeric values become 0.
    """
    number = pd.to_numeric(value, errors="coerce")

    if pd.isna(number):
        return 0.0

    return float(number)


# ============================================================
# STEP 1: CONNECT TO POSTGRESQL
# ============================================================

print("Connecting to PostgreSQL...")

conn = psycopg2.connect(
    host=DB_CONFIG["host"],
    port=DB_CONFIG["port"],
    dbname=DB_CONFIG["dbname"],
    user=DB_CONFIG["user"],
    password=DB_CONFIG["password"]
)

print("PostgreSQL connection successful.")


# ============================================================
# STEP 2: LOAD CATTLE DATA FROM DATABASE
# ============================================================

query = """
SELECT
    name,
    tag_number
FROM cattle_data
WHERE name IS NOT NULL
ORDER BY name;
"""

db_df = pd.read_sql_query(query, conn)

conn.close()

print(f"Loaded {len(db_df)} cattle records from PostgreSQL.")


# ============================================================
# STEP 3: CLEAN DATABASE NAMES
# ============================================================

db_df["original_name"] = db_df["name"].apply(clean_name)

db_df["tag_number"] = (
    db_df["tag_number"]
    .fillna("")
    .astype(str)
    .str.strip()
)


# Create a list for fuzzy matching.
database_names = db_df["original_name"].tolist()


# Mapping:
# cleaned database name -> database record
#
# Example:
# "નિતા" -> {
#     "name": "નિતા",
#     "tag_number": "SOM-068"
# }

db_lookup = {}

for _, row in db_df.iterrows():

    db_name = row["original_name"]

    db_lookup[db_name] = {
        "name": db_name,
        "tag_number": row["tag_number"]
    }


# ============================================================
# STEP 4: NAME MATCHING FUNCTION
# ============================================================

name_match_cache = {}


def find_database_cattle(excel_name):
    """
    Find the related cattle from PostgreSQL.

    Matching order:
    1. Exact match
    2. Fuzzy match

    Returns:
        tag_number
        related_name
    """

    excel_name = clean_name(excel_name)

    if not excel_name:
        return "Unknown Tag", "No Related Name"

    # --------------------------------------------------------
    # Use cache so the same cattle name is not matched again.
    # --------------------------------------------------------

    if excel_name in name_match_cache:
        return name_match_cache[excel_name]


    # --------------------------------------------------------
    # 1. EXACT MATCH
    # --------------------------------------------------------

    if excel_name in db_lookup:

        result = (
            db_lookup[excel_name]["tag_number"],
            db_lookup[excel_name]["name"]
        )

        name_match_cache[excel_name] = result

        return result


    # --------------------------------------------------------
    # 2. FUZZY MATCH
    # --------------------------------------------------------

    match = process.extractOne(
        excel_name,
        database_names,
        scorer=fuzz.ratio
    )

    if match is not None:

        matched_name = match[0]
        score = match[1]

        if score >= FUZZY_THRESHOLD:

            result = (
                db_lookup[matched_name]["tag_number"]
                if db_lookup[matched_name]["tag_number"]
                else "Unknown Tag",

                matched_name
            )

            name_match_cache[excel_name] = result

            print(
                f"FUZZY MATCH: "
                f"'{excel_name}' -> "
                f"'{matched_name}' "
                f"(score={score:.1f})"
            )

            return result


    # --------------------------------------------------------
    # NO MATCH
    # --------------------------------------------------------

    result = (
        "Unknown Tag",
        "No Related Name"
    )

    name_match_cache[excel_name] = result

    print(
        f"NO MATCH: '{excel_name}'"
    )

    return result


# ============================================================
# STEP 5: LOAD EXCEL WORKBOOK
# ============================================================

print("\nReading Excel workbook...")

excel = pd.ExcelFile(INPUT_FILE)

print("\nSheets found:")

for sheet in excel.sheet_names:
    print(f"  {sheet}")


# ============================================================
# STEP 6: PROCESS ALL 12 MONTHS
# ============================================================

all_rows = []


for sheet_name in excel.sheet_names:

    print("\n" + "=" * 70)
    print(f"Processing sheet: {sheet_name}")
    print("=" * 70)

    # Read without headers because:
    #
    # Row 0 = cattle names
    # Row 1 = સવાર / સાંજ
    # Row 2+ = daily data

    df = pd.read_excel(
        INPUT_FILE,
        sheet_name=sheet_name,
        header=None
    )

    if df.empty:
        print("Sheet is empty. Skipping.")
        continue


    # --------------------------------------------------------
    # FIRST TWO ROWS
    # --------------------------------------------------------

    name_row = df.iloc[0]
    shift_row = df.iloc[1]


    # --------------------------------------------------------
    # FIND DATE COLUMN
    # --------------------------------------------------------

    date_column = 0


    # --------------------------------------------------------
    # FIND CATTLE COLUMNS
    #
    # Structure:
    #
    # Column 0 = Date
    #
    # Column 1 = Cattle A - સવાર
    # Column 2 = Cattle A - સાંજ
    #
    # Column 3 = Cattle B - સવાર
    # Column 4 = Cattle B - સાંજ
    #
    # etc.
    # --------------------------------------------------------

    column_count = len(df.columns)

    col = 1

    while col < column_count:

        excel_name = name_row.iloc[col]

        # If column has no cattle name,
        # move to the next column.
        if pd.isna(excel_name):

            col += 1
            continue


        excel_name = clean_name(excel_name)


        # We expect the next column to be સાંજ.
        if col + 1 >= column_count:
            break


        morning_column = col
        evening_column = col + 1


        morning_label = clean_name(
            shift_row.iloc[morning_column]
        )

        evening_label = clean_name(
            shift_row.iloc[evening_column]
        )


        # ----------------------------------------------------
        # Validate that these are સવાર / સાંજ columns.
        # ----------------------------------------------------

        if morning_label != "સવાર":

            col += 1
            continue

        if evening_label != "સાંજ":

            col += 1
            continue


        # ----------------------------------------------------
        # FIND POSTGRESQL MATCH
        # ----------------------------------------------------

        tag_number, related_name = find_database_cattle(
            excel_name
        )


        # ----------------------------------------------------
        # PROCESS EACH DATE
        # ----------------------------------------------------

        for row_number in range(2, len(df)):

            raw_date = df.iloc[row_number, date_column]

            if pd.isna(raw_date):
                continue


            # Convert date to pandas datetime

            date_value = pd.to_datetime(
                raw_date,
                errors="coerce"
            )

            if pd.isna(date_value):
                print(
                    f"Invalid date in "
                    f"{sheet_name}, row {row_number + 1}: "
                    f"{raw_date}"
                )
                continue


            # ------------------------------------------------
            # MORNING MILK
            # ------------------------------------------------

            morning_milk = clean_number(
                df.iloc[row_number, morning_column]
            )


            # ------------------------------------------------
            # EVENING MILK
            # ------------------------------------------------

            evening_milk = clean_number(
                df.iloc[row_number, evening_column]
            )


            # ------------------------------------------------
            # TOTAL MILK
            # ------------------------------------------------

            total_milk = morning_milk + evening_milk


            # ------------------------------------------------
            # ADD RESULT
            # ------------------------------------------------

            all_rows.append({

                "name": excel_name,

                "tag_number": tag_number,

                "related_name": related_name,

                "date": date_value.strftime("%Y-%m-%d"),

                "milk": round(total_milk, 2)
            })


        # Move to next cattle
        col += 2


# ============================================================
# STEP 7: CREATE FINAL DATAFRAME
# ============================================================

print("\nCreating final dataframe...")

result_df = pd.DataFrame(
    all_rows,
    columns=[
        "name",
        "tag_number",
        "related_name",
        "date",
        "milk"
    ]
)


# ============================================================
# STEP 8: SORT DATA
#
# Sort by:
#   name
#   date
# ============================================================

if not result_df.empty:

    result_df["_date_sort"] = pd.to_datetime(
        result_df["date"],
        errors="coerce"
    )

    result_df = result_df.sort_values(
        by=["name", "_date_sort"],
        kind="stable"
    )

    result_df = result_df.drop(
        columns=["_date_sort"]
    )

    result_df = result_df.reset_index(
        drop=True
    )


# ============================================================
# STEP 9: WRITE XLSX
# ============================================================

print("\nWriting output file...")

with pd.ExcelWriter(
    OUTPUT_FILE,
    engine="openpyxl"
) as writer:

    result_df.to_excel(
        writer,
        sheet_name="Milk Data",
        index=False
    )


# ============================================================
# STEP 10: SUMMARY
# ============================================================

print("\n" + "=" * 70)
print("PROCESSING COMPLETED")
print("=" * 70)

print(f"Input file       : {INPUT_FILE}")
print(f"Output file      : {OUTPUT_FILE}")
print(f"Total rows       : {len(result_df)}")

if not result_df.empty:

    print(
        f"Unique cattle    : "
        f"{result_df['name'].nunique()}"
    )

    matched_tags = (
        result_df["tag_number"] != "Unknown Tag"
    ).sum()

    unmatched_tags = (
        result_df["tag_number"] == "Unknown Tag"
    ).sum()

    print(
        f"Rows with tag    : {matched_tags}"
    )

    print(
        f"Rows without tag : {unmatched_tags}"
    )

    print("\nUnmatched cattle:")

    unmatched_names = (
        result_df.loc[
            result_df["related_name"] == "No Related Name",
            "name"
        ]
        .drop_duplicates()
        .tolist()
    )

    if unmatched_names:

        for name in unmatched_names:
            print(f"  - {name}")

    else:

        print("  None")


print("\nDone.")