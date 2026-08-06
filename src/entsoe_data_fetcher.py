#!/usr/bin/env python3
"""
Moduł do pobierania danych z ENTSO-E Transparency Platform.
API Documentation: https://transparency.entsoe.eu/content/static_content/Static%20content/web%20api/Guide.html
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv

# Załaduj zmienne środowiskowe z pliku .env
load_dotenv()


class ENTSOEDataFetcher:
    """Klasa do pobierania danych o produkcji energii z ENTSO-E Transparency Platform."""
    
    API_ENDPOINT = "https://web-api.tp.entsoe.eu/api"
    AREA_CODE_POLAND = "10YPL-AREA-----S"
    
    # Mapowanie typów produkcji zgodnie z ENTSO-E
    PRODUCTION_TYPES = {
        'biomass': 'B01',  # Biomasa
        'lignite': 'B02',  # Węgiel brunatny
        'gas': 'B04',  # Gaz
        'hard_coal': 'B05',  # Węgiel kamienny
        'hydro_pumped': 'B10',  # Magazyny energii (pompowe)
        'hydro_run_of_river': 'B11',  # Woda (przepływowa)
        'hydro_reservoir': 'B12',  # Woda (zbiornikowa)
        'solar': 'B16',  # Słońce
        'wind_offshore': 'B18',  # Wiatr morski / offshore
        'wind_onshore': 'B19',  # Wiatr lądowy / onshore
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Inicjalizacja z kluczem API.
        
        Args:
            api_key: Klucz API ENTSO-E. Jeśli None, szuka w zmiennych środowiskowych.
        """
        self.api_key = api_key or os.getenv('ENTSOE_API_KEY')
        
        if not self.api_key:
            raise ValueError(
                "Klucz API ENTSO-E jest wymagany!\n"
                "Ustaw zmienną środowiskową ENTSOE_API_KEY lub przekaż api_key do konstruktora.\n"
                "Zarejestruj się na: https://transparency.entsoe.eu/"
            )
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; PSE-Energy-Scraper/1.3.0)',
        })
    
    def fetch_generation_data(self, date_from: str, date_to: str) -> Optional[pd.DataFrame]:
        """
        Pobiera dane o generacji energii dla wszystkich typów źródeł.
        UWAGA: Daty są interpretowane jako czas polski (Europe/Warsaw, UTC+1).
        
        Dla długich okresów (>365 dni) automatycznie dzieli na mniejsze fragmenty.
        
        Args:
            date_from: Data początkowa w formacie YYYY-MM-DD (w czasie polskim)
            date_to: Data końcowa w formacie YYYY-MM-DD (w czasie polskim)
            
        Returns:
            DataFrame z danymi o generacji lub None w przypadku błędu
        """
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d')
            dt_to = datetime.strptime(date_to, '%Y-%m-%d')
            
            # Sprawdź czy okres jest dłuższy niż ~1 rok (limit API)
            # API ENTSO-E zwykle ma limit 1 rok, więc dzielimy od 350+ dni dla bezpieczeństwa
            days_diff = (dt_to - dt_from).days
            
            if days_diff > 350:
                # Podziel na roczne fragmenty (max 365 dni każdy)
                print(f"📥 Pobieranie danych ENTSO-E dla okresu {date_from} - {date_to}...")
                print(f"   ⏳ Okres {days_diff} dni - dzielę na {(days_diff // 350) + 1} fragmenty...")
                
                all_chunks = []
                current_date = dt_from
                
                while current_date < dt_to:
                    chunk_end = min(current_date + timedelta(days=350), dt_to)
                    chunk_from = current_date.strftime('%Y-%m-%d')
                    chunk_to = chunk_end.strftime('%Y-%m-%d')
                    
                    print(f"   📦 Fragment: {chunk_from} - {chunk_to}")
                    df_chunk = self._fetch_single_period(chunk_from, chunk_to)
                    
                    if df_chunk is not None and not df_chunk.empty:
                        all_chunks.append(df_chunk)
                    
                    current_date = chunk_end + timedelta(days=1)
                
                if all_chunks:
                    df_combined = pd.concat(all_chunks, ignore_index=True)
                    # Usuń duplikaty (może być na styku okresów)
                    df_combined = df_combined.drop_duplicates(subset=['Data']).reset_index(drop=True)
                    print(f"✓ Pobrano łącznie {len(df_combined)} rekordów z ENTSO-E")
                    return df_combined
                else:
                    print("⚠️  Brak danych z ENTSO-E")
                    return None
            else:
                # Pojedyncze zapytanie dla krótkiego okresu
                print(f"📥 Pobieranie danych ENTSO-E dla okresu {date_from} - {date_to}...")
                return self._fetch_single_period(date_from, date_to)
                
        except Exception as e:
            print(f"❌ Błąd podczas pobierania danych z ENTSO-E: {e}")
            return None
    
    def _fetch_single_period(self, date_from: str, date_to: str) -> Optional[pd.DataFrame]:
        """
        Pobiera dane dla pojedynczego okresu (maksymalnie 1 rok).
        
        Args:
            date_from: Data początkowa w formacie YYYY-MM-DD
            date_to: Data końcowa w formacie YYYY-MM-DD
            
        Returns:
            DataFrame z danymi lub None
        """
        try:
            # Konwersja dat do formatu ENTSO-E (YYYYMMDDHHMM)
            # Dla czasu polskiego (UTC+1) musimy pobrać dane od UTC-1
            dt_from = datetime.strptime(date_from, '%Y-%m-%d')
            dt_to = datetime.strptime(date_to, '%Y-%m-%d')
            
            # Dla pojedynczego dnia w czasie polskim musimy uwzględnić offset UTC
            # CEST (lato): UTC+2, więc 00:00 CEST = 22:00 UTC poprzedniego dnia
            # CET (zima): UTC+1, więc 00:00 CET = 23:00 UTC poprzedniego dnia
            import pytz
            poland_tz = pytz.timezone('Europe/Warsaw')
            
            # Sprawdź offset UTC dla początku okresu
            dt_from_local = poland_tz.localize(dt_from)
            utc_offset_hours = int(dt_from_local.utcoffset().total_seconds() / 3600)
            
            # Pobierz dane z odpowiednim offsetem
            dt_from_utc = dt_from - timedelta(hours=utc_offset_hours)
            dt_to_utc = dt_to + timedelta(days=1) - timedelta(hours=utc_offset_hours)
            
            period_start = dt_from_utc.strftime('%Y%m%d%H%M')
            period_end = dt_to_utc.strftime('%Y%m%d%H%M')
            
            # Parametry zapytania
            params = {
                'securityToken': self.api_key,
                'documentType': 'A75',  # Actual generation per type
                'processType': 'A16',  # Realised
                'in_Domain': self.AREA_CODE_POLAND,
                'periodStart': period_start,
                'periodEnd': period_end
            }
            
            response = self.session.get(self.API_ENDPOINT, params=params, timeout=60)
            
            if response.status_code == 200:
                # Parsuj XML
                df = self._parse_xml_response(response.content, date_from, date_to)
                if df is not None and not df.empty:
                    return df
                else:
                    return None
            elif response.status_code == 401:
                print("❌ Błąd autoryzacji - sprawdź klucz API ENTSO-E")
                return None
            elif response.status_code == 400:
                print(f"⚠️  Błąd 400 - okres może być zbyt długi lub dane niedostępne")
                return None
            else:
                print(f"⚠️  Błąd API ENTSO-E: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"⚠️  Błąd podczas pobierania fragmentu: {e}")
            return None
    
    def _parse_xml_response(self, xml_content: bytes, date_from: str, date_to: str) -> Optional[pd.DataFrame]:
        """
        Parsuje odpowiedź XML z ENTSO-E do DataFrame.
        
        Args:
            xml_content: Zawartość XML z API
            date_from: Data początkowa (do filtrowania)
            date_to: Data końcowa (do filtrowania)
            
        Returns:
            DataFrame z danymi czasowymi
        """
        try:
            root = ET.fromstring(xml_content)
            
            # Namespace ENTSO-E
            ns = {'ns': 'urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0'}
            
            all_data = []
            
            # Iteruj po TimeSeries (każdy typ produkcji)
            for timeseries in root.findall('.//ns:TimeSeries', ns):
                # Pobierz typ produkcji
                psr_type_elem = timeseries.find('.//ns:MktPSRType/ns:psrType', ns)
                if psr_type_elem is None:
                    continue
                    
                psr_type = psr_type_elem.text
                
                # Mapuj kod ENTSO-E na czytelną nazwę
                type_name = self._get_type_name(psr_type)
                
                # Pobierz punkty czasowe
                for period in timeseries.findall('.//ns:Period', ns):
                    start_time_elem = period.find('.//ns:timeInterval/ns:start', ns)
                    if start_time_elem is None:
                        continue
                        
                    start_time = datetime.fromisoformat(start_time_elem.text.replace('Z', '+00:00'))
                    resolution_elem = period.find('.//ns:resolution', ns)
                    resolution = resolution_elem.text if resolution_elem is not None else 'PT60M'
                    
                    # Parsuj interwał (np. PT15M = 15 minut, PT60M = 60 minut)
                    interval_minutes = self._parse_resolution(resolution)
                    
                    # Pobierz punkty danych
                    for point in period.findall('.//ns:Point', ns):
                        position_elem = point.find('ns:position', ns)
                        quantity_elem = point.find('ns:quantity', ns)
                        
                        if position_elem is None or quantity_elem is None:
                            continue
                        
                        position = int(position_elem.text)
                        quantity = float(quantity_elem.text)
                        
                        # Oblicz timestamp
                        timestamp = start_time + timedelta(minutes=(position - 1) * interval_minutes)
                        
                        all_data.append({
                            'Data': timestamp,
                            'Typ': type_name,
                            'Moc [MW]': quantity
                        })
            
            if not all_data:
                return None
            
            df = pd.DataFrame(all_data)
            
            # Pivot - zamień typy produkcji na kolumny
            df_pivot = df.pivot_table(
                index='Data',
                columns='Typ',
                values='Moc [MW]',
                aggfunc='first'
            ).reset_index()
            
            # Dodaj brakujące kolumny i wypełnij NaN zerami
            expected_columns = [
                'Węgiel kamienny [MW]',
                'Węgiel brunatny [MW]',
                'Gaz [MW]',
                'Wiatr offshore [MW]',
                'Wiatr onshore [MW]',
                'Słońce [MW]',
                'Woda (przepływowa) [MW]',
                'Woda (zbiornikowa) [MW]',
                'Magazyny energii [MW]',
                'Biomasa [MW]'
            ]
            
            for col in expected_columns:
                if col not in df_pivot.columns:
                    df_pivot[col] = 0.0
            
            # Oblicz sumę wody
            df_pivot['Woda [MW]'] = (
                df_pivot['Woda (przepływowa) [MW]'].fillna(0) + 
                df_pivot['Woda (zbiornikowa) [MW]'].fillna(0)
            )

            # Oblicz sumę wiatru offshore + onshore
            df_pivot['Wiatr [MW]'] = (
                df_pivot['Wiatr offshore [MW]'].fillna(0) +
                df_pivot['Wiatr onshore [MW]'].fillna(0)
            )
            
            df_pivot.fillna(0, inplace=True)
            
            # Konwertuj timestampy UTC na czas polski (Europe/Warsaw)
            df_pivot['Data'] = pd.to_datetime(df_pivot['Data'])
            df_pivot['Data'] = df_pivot['Data'].dt.tz_convert('Europe/Warsaw')
            
            # USUNIĘTO filtrowanie po dacie - pobieramy wszystkie dane z API
            # API już zwraca dane dla żądanego okresu (period_start/period_end)
            # Dodatkowe filtrowanie powodowało utratę godziny 0 (00:00-00:45)
            
            return df_pivot
            
        except Exception as e:
            print(f"❌ Błąd parsowania XML: {e}")
            return None
    
    def _get_type_name(self, psr_type: str) -> str:
        """Mapuje kod typu produkcji ENTSO-E na czytelną nazwę."""
        type_mapping = {
            'B01': 'Biomasa [MW]',
            'B02': 'Węgiel brunatny [MW]',
            'B04': 'Gaz [MW]',
            'B05': 'Węgiel kamienny [MW]',
            'B10': 'Magazyny energii [MW]',
            'B11': 'Woda (przepływowa) [MW]',
            'B12': 'Woda (zbiornikowa) [MW]',
            'B16': 'Słońce [MW]',
            'B18': 'Wiatr offshore [MW]',
            'B19': 'Wiatr onshore [MW]',
        }
        return type_mapping.get(psr_type, f'Nieznany typ ({psr_type}) [MW]')
    
    def _parse_resolution(self, resolution: str) -> int:
        """Parsuje resolution string (np. PT15M) na minuty."""
        if 'PT' in resolution:
            resolution = resolution.replace('PT', '')
            if 'M' in resolution:
                return int(resolution.replace('M', ''))
            elif 'H' in resolution:
                return int(resolution.replace('H', '')) * 60
        return 60  # Domyślnie 60 minut


def main():
    """Funkcja testowa."""
    print("=" * 70)
    print("ENTSO-E Data Fetcher - Test")
    print("=" * 70)
    print()
    
    # Sprawdź czy klucz API jest ustawiony
    api_key = os.getenv('ENTSOE_API_KEY')
    if not api_key:
        print("⚠️  Brak klucza API ENTSO-E!")
        print()
        print("Aby użyć tego modułu:")
        print("1. Zarejestruj się na https://transparency.entsoe.eu/")
        print("2. Pobierz klucz API (Account Settings -> Web API Security Token)")
        print("3. Ustaw zmienną środowiskową:")
        print("   export ENTSOE_API_KEY='twój_klucz_api'")
        print()
        return
    
    try:
        fetcher = ENTSOEDataFetcher()
        df = fetcher.fetch_generation_data('2025-01-15', '2025-01-15')
        
        if df is not None:
            print("\n📊 Przykładowe dane:")
            print("-" * 70)
            print(df.head(10).to_string())
            print(f"\nŁączna liczba rekordów: {len(df)}")
        else:
            print("\n⚠️  Nie udało się pobrać danych")
    except Exception as e:
        print(f"\n❌ Błąd: {e}")


if __name__ == "__main__":
    main()
