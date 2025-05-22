import pandas as pd
import json
import os

# --- Configuration ---
EXCEL_FILENAME = 'GuessYearList.xlsx'          # Name of your Excel file
JSON_FILENAME = 'guess_the_year_questions.json' # Output JSON file
YEAR_COLUMN = 'year'                            # Name of the year column
# <<< MODIFIED: Added 'image_url' >>>
EXPECTED_COLUMNS = ['question', 'year', 'category', 'image_url'] # Expected columns

# --- Get the directory of the script ---
script_dir = os.path.dirname(os.path.abspath(__file__))
excel_path = os.path.join(script_dir, EXCEL_FILENAME)
json_path = os.path.join(script_dir, JSON_FILENAME)

print(f"Attempting to read Excel file: {excel_path}")

try:
    # Read the Excel file
    df = pd.read_excel(excel_path)

    print(f"Successfully read {len(df)} rows from Excel.")

    # --- Data Validation and Cleaning ---
    # Check for expected columns
    missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing expected columns: {', '.join(missing_cols)}")

    # Select only expected columns (this ensures order and includes image_url)
    df = df[EXPECTED_COLUMNS]

    # Clean text columns
    df['question'] = df['question'].fillna('').astype(str).str.strip()
    df['category'] = df['category'].fillna('').astype(str).str.strip()
    # <<< NEW: Clean image_url column >>>
    df['image_url'] = df['image_url'].fillna('').astype(str).str.strip()

    # Validate and convert 'year' column
    original_row_count = len(df)
    df[YEAR_COLUMN] = pd.to_numeric(df[YEAR_COLUMN], errors='coerce')
    df = df.dropna(subset=[YEAR_COLUMN])
    df[YEAR_COLUMN] = df[YEAR_COLUMN].astype(int)
    valid_rows = len(df)
    skipped_rows = original_row_count - valid_rows
    print(f"Processed {valid_rows} valid rows, skipped {skipped_rows} rows due to non-numeric year values.")

    # Remove rows with empty questions after stripping whitespace
    df = df[df['question'] != '']
    # Optional: Remove rows with empty image URLs if desired
    # df = df[df['image_url'] != '']

    if df.empty:
        print("ERROR: No valid data remaining after cleaning and year validation.")
    else:
        # Convert DataFrame to list of dictionaries
        data_list = df.to_dict(orient='records')

        # Write to JSON file
        print(f"Writing {len(data_list)} records (including image_url) to {json_path}...")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, ensure_ascii=False, indent=2)

        print(f"Successfully converted Excel data to {JSON_FILENAME}")

except FileNotFoundError:
    print(f"ERROR: Excel file not found at '{excel_path}'.")
except ValueError as ve:
    print(f"ERROR: Data validation failed - {ve}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")