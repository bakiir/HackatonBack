import pandas as pd
import logging
from typing import Tuple, Dict

class ExcelHandler:
    @staticmethod
    def load_exam_data(file_path: str) -> pd.DataFrame:
        try:
            df = pd.read_excel(file_path)
            logging.info(f"Successfully loaded exam data from {file_path}")
            return df
        except Exception as e:
            logging.error(f"Error loading exam data: {str(e)}")
            raise

    @staticmethod
    def load_room_data(file_path: str) -> pd.DataFrame:
        try:
            df = pd.read_excel(file_path)
            logging.info(f"Successfully loaded room data from {file_path}")
            return df
        except Exception as e:
            logging.error(f"Error loading room data: {str(e)}")
            raise

    @staticmethod
    def export_to_excel(df: pd.DataFrame, output_path: str):
        try:
            df.to_excel(output_path, index=False)
            logging.info(f"Successfully exported data to {output_path}")
        except Exception as e:
            logging.error(f"Error exporting to excel: {str(e)}")
            raise
