import pandas as pd
import json
import os
from datetime import datetime

# --- Configuration ---
EXCEL_FILENAME = 'CelebrityList.xlsx'  # The name of your Excel file
JSON_FILENAME = 'celebrities.json'    # The name of the output JSON file
DATE_COLUMN = 'dob'                   # Name of the date of birth column
EXPECTED_COLUMNS = ['name', 'dob', 'image_url', 'description']

# --- Get the directory of the script ---
script_dir = os.path.dirname(os.path.abspath(__file__))
excel_path = os.path.join(script_dir, EXCEL_FILENAME)
json_path = os.path.join(script_dir, JSON_FILENAME)

print(f"Attempting to read Excel file: {excel_path}")

try:
    # Read the Excel file using pandas
    # Specify dtype={'dob': str} if dates are problematic, otherwise let pandas parse
    df = pd.read_excel(excel_path) # Reads the first sheet by default

    print(f"Successfully read {len(df)} rows from Excel.")

    # --- Data Validation and Cleaning ---
    # Check if all expected columns exist
    missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing expected columns in Excel file: {', '.join(missing_cols)}")

    # Select only the expected columns in the correct order (optional, but good practice)
    df = df[EXPECTED_COLUMNS]

    # Fill any missing values (NaN) with empty strings for text columns or handle appropriately
    df['name'] = df['name'].fillna('').astype(str).str.strip()
    df['image_url'] = df['image_url'].fillna('').astype(str).str.strip()
    df['description'] = df['description'].fillna('').astype(str).str.strip()

    # Convert 'dob' column to datetime objects, then format as YYYY-MM-DD string
    # Handle potential errors during conversion
    formatted_dates = []
    valid_rows = 0
    skipped_rows = 0
    for index, row in df.iterrows():
        dob_raw = row[DATE_COLUMN]
        try:
            # Attempt to parse the date
            if pd.isna(dob_raw): # Handle empty cells
                 raise ValueError("Empty date")
            # Let pandas try to infer format, then specifically format
            dt_obj = pd.to_datetime(dob_raw)
            formatted_dates.append(dt_obj.strftime('%Y-%m-%d'))
            valid_rows += 1
        except Exception as e:
            print(f"  - WARNING: Could not parse date in row {index+2} (Value: '{dob_raw}'). Skipping row. Error: {e}")
            formatted_dates.append(None) # Mark row for removal
            skipped_rows +=1

    df[DATE_COLUMN] = formatted_dates # Assign formatted dates back
    df = df.dropna(subset=[DATE_COLUMN]) # Remove rows where date conversion failed

    print(f"Processed {valid_rows} valid rows, skipped {skipped_rows} rows due to date issues.")

    # Ensure no essential fields are empty after cleaning
    df = df[df['name'] != ''] # Remove rows with empty names

    if df.empty:
        print("ERROR: No valid data remaining after cleaning and date conversion.")
    else:
        # Convert DataFrame to a list of dictionaries
        data_list = df.to_dict(orient='records')

        # Write the list of dictionaries to the JSON file
        print(f"Writing {len(data_list)} records to {json_path}...")
        with open(json_path, 'w', encoding='utf-8') as f:
            # indent=2 makes the JSON file human-readable
            json.dump(data_list, f, ensure_ascii=False, indent=2)

        print(f"Successfully converted Excel data to {JSON_FILENAME}")

except FileNotFoundError:
    print(f"ERROR: Excel file not found at '{excel_path}'. Make sure '{EXCEL_FILENAME}' is in the same folder as the script.")
except ValueError as ve:
    print(f"ERROR: Data validation failed - {ve}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")