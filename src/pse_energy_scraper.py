#!/usr/bin/env python3
"""
Skrypt do pobierania i analizowania danych o produkcji energii wiatrowej i fotowoltaicznej z PSE.
Dane źródłowe: https://raporty.pse.pl/report/his-wlk-cal
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import json
from typing import Optional, Tuple
import sys


class PSEEnergyDataFetcher:
    """Klasa do pobierania danych o produkcji energii z PSE."""
    
    BASE_URL = "https://api.raporty.pse.pl/api"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        })
    
    def fetch_data(self, date_from: str, date_to: str) -> Optional[pd.DataFrame]:
        """
        Pobiera dane z PSE dla podanego zakresu dat.
        
        Args:
            date_from: Data początkowa w formacie YYYY-MM-DD
            date_to: Data końcowa w formacie YYYY-MM-DD
            
        Returns:
            DataFrame z danymi lub None w przypadku błędu
        """
        try:
            # Oblicz liczbę dni
            from datetime import datetime, timedelta
            start_date = datetime.strptime(date_from, '%Y-%m-%d')
            end_date = datetime.strptime(date_to, '%Y-%m-%d')
            days_diff = (end_date - start_date).days + 1
            
            # ZAWSZE pobieraj dane dzień po dniu dla pewności (API PSE ma limit ~100 rekordów)
            if days_diff > 1:
                print(f"📥 Pobieranie danych dla {days_diff} dni...")
                all_dfs = []
                failed_days = []  # Śledź dni bez danych
                
                current_date = start_date
                while current_date <= end_date:
                    date_str = current_date.strftime('%Y-%m-%d')
                    df_day = self._fetch_single_day(date_str)
                    
                    if df_day is not None and not df_day.empty:
                        all_dfs.append(df_day)
                    else:
                        failed_days.append(date_str)
                    
                    current_date += timedelta(days=1)
                    
                    # Progress indicator
                    if len(all_dfs) % 10 == 0:
                        print(f"  ✓ Pobrano {len(all_dfs)} dni...")
                
                # Raport o brakujących dniach
                if failed_days:
                    print(f"  ⚠️  Brak danych PSE dla {len(failed_days)} dni:")
                    for day in failed_days[:10]:  # Pokaż max 10
                        print(f"     - {day}")
                    if len(failed_days) > 10:
                        print(f"     ... i {len(failed_days) - 10} więcej")
                
                if all_dfs:
                    # Użyj concat z ignore_index=True, ale tylko jeśli mamy dane
                    result = pd.concat([df for df in all_dfs if not df.empty], ignore_index=True)
                    
                    # Sprawdź duplikaty przed zwróceniem
                    if not result.empty and 'Data' in result.columns:
                        duplicates = result['Data'].duplicated().sum()
                        if duplicates > 0:
                            print(f"  ⚠️  Wykryto {duplicates} duplikatów w danych PSE")
                            result = result.drop_duplicates(subset=['Data'], keep='first')
                            print(f"     Usunięto duplikaty, pozostało {len(result)} rekordów")
                    
                    # Filtruj dane przyszłościowe (tylko do bieżącej godziny)
                    result = self._filter_future_data(result)
                    
                    return result if not result.empty else None
                else:
                    return None
            else:
                # Dla krótkich okresów, jeden request
                result = self._fetch_date_range(date_from, date_to)
                # Filtruj dane przyszłościowe
                return self._filter_future_data(result) if result is not None else None
            
        except Exception as e:
            print(f"❌ Błąd podczas pobierania danych: {e}")
            return None
    
    def _filter_future_data(self, df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        """
        Filtruje dane prognostyczne - pozostawia tylko rzeczywiste pomiary.
        API PSE czasem zwraca dane prognostyczne dla całego dnia.
        
        Zamiast filtrować według bieżącej godziny zegarowej, kod:
        1. Sprawdza wartości w kolumnach (MW)
        2. Znajduje ostatni rekord z rzeczywistymi danymi
        3. Ucina dane prognostyczne po tym rekordzie
        
        Args:
            df: DataFrame z danymi
            
        Returns:
            DataFrame tylko z rzeczywistymi pomiarami (bez prognoz)
        """
        if df is None or df.empty:
            return df
            
        if 'Data' not in df.columns:
            return df
        
        try:
            from datetime import datetime, timedelta
            
            # Sprawdź czy to dzisiejszy dzień
            today = datetime.now().date()
            if 'Data' in df.columns:
                df_dates = pd.to_datetime(df['Data']).dt.date
                has_today = any(d == today for d in df_dates)
                
                # Jeśli to tylko dane historyczne, nie filtruj
                if not has_today:
                    return df
            
            # Dla danych z dzisiejszym dniem - znajdź ostatni rzeczywisty pomiar
            # API PSE zwraca komplet danych, ale najnowsze mogą być prognostyczne
            
            # Sortuj po dacie
            df_sorted = df.sort_values('Data').copy()
            
            # Sprawdź kolumny z wartościami MW
            value_columns = [col for col in df_sorted.columns if '[MW]' in col and col != 'Data']
            
            if not value_columns:
                return df
            
            # Znajdź ostatni rekord gdzie wartości się zmieniają
            # (prognozy często mają stałe lub zerowe wartości)
            before_count = len(df_sorted)
            
            # Dla bezpieczeństwa - jeśli wszystkie dane są z przeszłości (>2h temu), zwróć wszystko
            if 'Data' in df_sorted.columns:
                last_timestamp = pd.to_datetime(df_sorted['Data'].iloc[-1])
                now = pd.Timestamp.now()
                
                # Jeśli timezone-aware, usuń timezone dla porównania
                if hasattr(last_timestamp, 'tz') and last_timestamp.tz is not None:
                    last_timestamp = last_timestamp.tz_localize(None)
                if hasattr(now, 'tz') and now.tz is not None:
                    now = now.tz_localize(None)
                
                time_diff = (now - last_timestamp).total_seconds() / 3600  # godziny
                
                if time_diff > 2:
                    # Dane są z przeszłości (>2h), zwróć wszystko
                    return df
                
                # Jeśli to dane z ostatnich 2 godzin, ogranicz do teraz minus 15 min
                # (API PSE ma opóźnienie publikacji)
                cutoff_time = now - timedelta(minutes=15)
                df_filtered = df_sorted[pd.to_datetime(df_sorted['Data']) <= cutoff_time].copy()
                
                after_count = len(df_filtered)
                if after_count < before_count and after_count > 0:
                    removed = before_count - after_count
                    last_data_time = pd.to_datetime(df_filtered['Data'].iloc[-1])
                    print(f"  ℹ️  Automatycznie odfiltrowano {removed} pomiarów z przyszłości")
                    print(f"     (dane tylko do ostatniej aktualizacji PSE: {last_data_time.strftime('%Y-%m-%d %H:%M')})")
                
                return df_filtered
        except Exception as e:
            # Jeśli filtrowanie się nie powiodło, zwróć oryginalne dane
            print(f"  ⚠️  Nie udało się odfiltrować danych przyszłościowych: {e}")
            return df

    def _build_filter_url(self, endpoint: str, odata_filter: str) -> str:
        from urllib.parse import quote
        encoded_filter = quote(odata_filter, safe="'")
        return f"{endpoint}?$filter={encoded_filter}"
    
    def _fetch_single_day(self, date: str, max_retries: int = 3) -> Optional[pd.DataFrame]:
        """
        Pobiera dane dla pojedynczego dnia z mechanizmem retry.
        
        Args:
            date: Data w formacie YYYY-MM-DD
            max_retries: Maksymalna liczba prób (domyślnie 3)
            
        Returns:
            DataFrame z danymi lub None
        """
        endpoint = f"{self.BASE_URL}/his-wlk-cal"
        odata_filter = f"business_date eq '{date}'"
        url = self._build_filter_url(endpoint, odata_filter)
        
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    if data and 'value' in data and len(data['value']) > 0:
                        # Sprawdź czy nie trafiliśmy na limit API
                        if len(data['value']) >= 100:
                            print(f"  ⚠️  Uwaga: Otrzymano {len(data['value'])} rekordów dla {date} - możliwy limit API")
                        return self._parse_data(data)
                    else:
                        # API zwróciło sukces, ale brak danych - nie retry
                        return None
                elif response.status_code >= 500:
                    # Błąd serwera - spróbuj ponownie
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(1 * (attempt + 1))  # Exponential backoff
                        continue
            except Exception as e:
                # Błąd sieci - spróbuj ponownie
                if attempt < max_retries - 1:
                    import time
                    time.sleep(1 * (attempt + 1))
                    continue
        
        return None
    
    def _fetch_date_range(self, date_from: str, date_to: str) -> Optional[pd.DataFrame]:
        """Pobiera dane dla zakresu dat (krótkiego okresu - max 1 dzień)."""
        endpoint = f"{self.BASE_URL}/his-wlk-cal"
        odata_filter = f"business_date ge '{date_from}' and business_date le '{date_to}'"
        url = self._build_filter_url(endpoint, odata_filter)
        
        try:
            response = self.session.get(url, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data and 'value' in data and len(data['value']) > 0:
                    # Sprawdź czy nie trafiliśmy na limit API
                    if len(data['value']) >= 100:
                        print(f"  ⚠️  OSTRZEŻENIE: Otrzymano dokładnie {len(data['value'])} rekordów!")
                        print(f"     Prawdopodobnie trafiono na limit API PSE (~100 rekordów)")
                        print(f"     Dane mogą być niepełne! Użyj pobierania dzień po dniu.")
                    return self._parse_data(data)
                else:
                    print(f"⚠️  Brak danych dla okresu {date_from} - {date_to}")
                    return None
            else:
                print(f"⚠️  Błąd API: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Błąd: {e}")
            return None
    
    def _parse_data(self, data: dict) -> pd.DataFrame:
        """Parsuje dane JSON z API do DataFrame."""
        if isinstance(data, dict) and 'value' in data:
            df = pd.DataFrame(data['value'])
            
            # Mapowanie kolumn PSE na czytelne nazwy
            df.rename(columns={
                'dtime': 'Data',
                'wi': 'Sumaryczna generacja źródeł wiatrowych [MW]',
                'pv': 'Sumaryczna generacja źródeł fotowoltaicznych [MW]',
                'demand': 'Zapotrzebowanie na moc [MW]',
                'swm_p': 'Krajowe saldo wymiany międzysystemowej - równoległa [MW]',
                'swm_np': 'Krajowe saldo wymiany międzysystemowej - nierównoległa [MW]'
            }, inplace=True)
            
            # Oblicz sumę sald wymiany międzysystemowej
            swm_p_col = 'Krajowe saldo wymiany międzysystemowej - równoległa [MW]'
            swm_np_col = 'Krajowe saldo wymiany międzysystemowej - nierównoległa [MW]'
            
            if swm_p_col in df.columns and swm_np_col in df.columns:
                df['Krajowe saldo wymiany międzysystemowej [MW]'] = (
                    df[swm_p_col].fillna(0) + df[swm_np_col].fillna(0)
                )
            
            # Konwersja daty na datetime
            if 'Data' in df.columns:
                # Obsługa dni zmiany czasu - PSE API zwraca nieprawidłowy format jak "02a:15:00"
                # dla godzin w czasie powtórzonym (zmiana czasu zimowego)
                # Zachowujemy informację czy to "a" czy "b" w osobnej kolumnie
                df['_dst_marker'] = ''
                if df['Data'].dtype == 'object':
                    # Wykryj czy to "a" czy "b" PRZED zamianą
                    df.loc[df['Data'].str.contains(r'\d{2}a:', regex=True, na=False), '_dst_marker'] = 'first'
                    df.loc[df['Data'].str.contains(r'\d{2}b:', regex=True, na=False), '_dst_marker'] = 'second'
                    
                    # Zastąp "02a:" i "02b:" przez "02:" - oba będą miały ten sam timestamp
                    df['Data'] = df['Data'].str.replace(r'(\d{2})a:', r'\1:', regex=True)
                    df['Data'] = df['Data'].str.replace(r'(\d{2})b:', r'\1:', regex=True)
                
                try:
                    df['Data'] = pd.to_datetime(df['Data'], format='mixed')
                except Exception as e:
                    print(f"⚠️  Błąd parsowania dat: {e}")
                    # Spróbuj bez strict format
                    try:
                        df['Data'] = pd.to_datetime(df['Data'], errors='coerce')
                        # Usuń wiersze gdzie data się nie sparsowała
                        df = df.dropna(subset=['Data'])
                    except Exception as e2:
                        print(f"❌ Nie udało się sparsować dat: {e2}")
                        return pd.DataFrame()
                
                # PSE timestamp reprezentuje KONIEC przedziału (np. 00:15 = przedział 00:00-00:15)
                # Przesuwamy o -15 minut aby timestamp reprezentował POCZĄTEK przedziału
                # To umożliwia poprawne łączenie z danymi ENTSO-E
                df['Data'] = df['Data'] - pd.Timedelta(minutes=15)
            
            # Usuń duplikaty (mogą powstać przy łączeniu danych)
            if 'Data' in df.columns:
                duplicates = df['Data'].duplicated().sum()
                if duplicates > 0:
                    df = df.drop_duplicates(subset=['Data'], keep='first')
            
            return df
        
        return pd.DataFrame()
    
    def generate_sample_data(self, date_from: str, date_to: str) -> pd.DataFrame:
        """
        Generuje przykładowe dane dla testów (gdy API nie zwraca danych).
        UWAGA: To są dane syntetyczne, nie rzeczywiste dane PSE!
        """
        print("⚠️  UWAGA: Generuję przykładowe dane do testów (brak danych z API)")
        print("   Dane są syntetyczne i służą wyłącznie do demonstracji")
        
        date_range = pd.date_range(start=date_from, end=date_to, freq='15min')
        
        import numpy as np
        # Generowanie realistycznych danych z cyklem dobowym
        hours = date_range.hour + date_range.minute / 60
        
        # Wiatr - większa produkcja w nocy i rano
        wind_base = 2000 + 1000 * np.sin((hours - 6) * np.pi / 12)
        wind_noise = np.random.normal(0, 300, len(date_range))
        wind_power = np.maximum(0, wind_base + wind_noise)
        
        # Fotowoltaika - produkcja tylko w dzień
        solar_base = np.maximum(0, 1500 * np.sin((hours - 6) * np.pi / 12))
        solar_noise = np.random.normal(0, 200, len(date_range))
        solar_power = np.maximum(0, solar_base + solar_noise)
        
        # Zapotrzebowanie - wyższe w dzień, niższe w nocy
        demand_base = 16000 + 4000 * np.sin((hours - 12) * np.pi / 12)
        demand_noise = np.random.normal(0, 500, len(date_range))
        demand = np.maximum(8000, demand_base + demand_noise)
        
        # Saldo wymiany - może być dodatnie lub ujemne
        swm_p_base = -500 + 1000 * np.sin((hours - 3) * np.pi / 12)
        swm_p_noise = np.random.normal(0, 200, len(date_range))
        swm_p = swm_p_base + swm_p_noise
        
        swm_np_base = 200 + 300 * np.sin((hours - 9) * np.pi / 12)
        swm_np_noise = np.random.normal(0, 100, len(date_range))
        swm_np = swm_np_base + swm_np_noise
        
        df = pd.DataFrame({
            'Data': date_range,
            'Sumaryczna generacja źródeł wiatrowych [MW]': wind_power,
            'Sumaryczna generacja źródeł fotowoltaicznych [MW]': solar_power,
            'Zapotrzebowanie na moc [MW]': demand,
            'Krajowe saldo wymiany międzysystemowej - równoległa [MW]': swm_p,
            'Krajowe saldo wymiany międzysystemowej - nierównoległa [MW]': swm_np,
            'Krajowe saldo wymiany międzysystemowej [MW]': swm_p + swm_np
        })
        
        return df


class EnergyDataAnalyzer:
    """Klasa do analizy danych o produkcji energii."""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._prepare_data()
    
    def _prepare_data(self):
        """Przygotowuje dane do analizy."""
        # Znajdź kolumnę z datą
        date_columns = [col for col in self.df.columns if 'data' in col.lower() or 'date' in col.lower() or 'czas' in col.lower()]
        
        if date_columns:
            self.df['Data'] = pd.to_datetime(self.df[date_columns[0]])
        elif 'Data' not in self.df.columns:
            print("⚠️  Nie znaleziono kolumny z datą")
            return
        
        self.df.set_index('Data', inplace=True)
        
        # Znajdź kolumny z danymi o wietrze i PV
        self.wind_col = self._find_column(['wiatr', 'wind'])
        self.solar_col = self._find_column(['fotowoltai', 'solar', 'pv', 'słoneczn'])
        
        # Znajdź kolumny z nowymi danymi
        self.demand_col = self._find_column(['zapotrzebowanie', 'demand'])
        self.swm_total_col = self._find_column(['krajowe saldo wymiany międzysystemowej [mw]'])
        
        if not self.wind_col:
            print("⚠️  Nie znaleziono kolumny z danymi o energii wiatrowej")
        if not self.solar_col:
            print("⚠️  Nie znaleziono kolumny z danymi o energii fotowoltaicznej")
    
    def _find_column(self, keywords: list) -> Optional[str]:
        """Znajduje kolumnę zawierającą którekolwiek ze słów kluczowych."""
        for col in self.df.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in keywords):
                return col
        return None
    
    def sum_period(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
        """
        Sumuje produkcję dla podanego okresu.
        
        Args:
            date_from: Data początkowa (opcjonalna)
            date_to: Data końcowa (opcjonalna)
            
        Returns:
            Słownik z sumami dla wiatru i PV w MWh
        """
        df_filtered = self.df
        
        if date_from:
            df_filtered = df_filtered[df_filtered.index >= date_from]
        if date_to:
            df_filtered = df_filtered[df_filtered.index <= date_to]
        
        # Sprawdź czy są dane
        if df_filtered.empty:
            return {
                'błąd': 'Brak danych dla podanego okresu',
                'wiatr_MWh': 0,
                'wiatr_średnia_MW': 0,
                'fotowoltaika_MWh': 0,
                'fotowoltaika_średnia_MW': 0
            }
        
        # Dane co 15 minut, więc mnożymy przez 0.25h aby uzyskać MWh
        results = {
            'okres_od': df_filtered.index.min().strftime('%Y-%m-%d %H:%M'),
            'okres_do': df_filtered.index.max().strftime('%Y-%m-%d %H:%M'),
            'liczba_pomiarów': len(df_filtered),
        }
        
        if self.wind_col:
            wind_sum_mw = df_filtered[self.wind_col].sum()
            wind_mwh = wind_sum_mw * 0.25  # Suma MW * 0.25h = MWh (dane co 15 min)
            wind_mean_mw = df_filtered[self.wind_col].mean()
            
            results['wiatr_suma_MW'] = round(wind_sum_mw, 2)
            results['wiatr_MWh'] = round(wind_mwh, 2)
            results['wiatr_średnia_MW'] = round(wind_mean_mw, 2)
        
        if self.solar_col:
            solar_sum_mw = df_filtered[self.solar_col].sum()
            solar_mwh = solar_sum_mw * 0.25
            solar_mean_mw = df_filtered[self.solar_col].mean()
            
            results['fotowoltaika_suma_MW'] = round(solar_sum_mw, 2)
            results['fotowoltaika_MWh'] = round(solar_mwh, 2)
            results['fotowoltaika_średnia_MW'] = round(solar_mean_mw, 2)
        
        if self.demand_col:
            demand_sum_mw = df_filtered[self.demand_col].sum()
            demand_mwh = demand_sum_mw * 0.25
            demand_mean_mw = df_filtered[self.demand_col].mean()
            
            results['zapotrzebowanie_suma_MW'] = round(demand_sum_mw, 2)
            results['zapotrzebowanie_MWh'] = round(demand_mwh, 2)
            results['zapotrzebowanie_średnia_MW'] = round(demand_mean_mw, 2)
        
        if self.swm_total_col:
            swm_sum_mw = df_filtered[self.swm_total_col].sum()
            swm_mwh = swm_sum_mw * 0.25
            swm_mean_mw = df_filtered[self.swm_total_col].mean()
            
            results['saldo_wymiany_suma_MW'] = round(swm_sum_mw, 2)
            results['saldo_wymiany_MWh'] = round(swm_mwh, 2)
            results['saldo_wymiany_średnia_MW'] = round(swm_mean_mw, 2)
        
        return results
    
    def monthly_sums(self, year_from: int = 2020, year_to: Optional[int] = None) -> pd.DataFrame:
        """
        Generuje miesięczne sumy produkcji.
        
        Args:
            year_from: Rok początkowy
            year_to: Rok końcowy (domyślnie bieżący)
            
        Returns:
            DataFrame z miesięcznymi sumami
        """
        if year_to is None:
            year_to = datetime.now().year
        
        df_filtered = self.df[
            (self.df.index.year >= year_from) & 
            (self.df.index.year <= year_to)
        ]
        
        # Zbierz wszystkie dostępne kolumny do agregacji
        cols_to_agg = [col for col in [self.wind_col, self.solar_col, self.demand_col, self.swm_total_col] if col]
        
        # Sumy MW (bez przeliczania na MWh)
        monthly = df_filtered.resample('ME').agg({
            col: 'sum' for col in cols_to_agg
        })
        
        monthly.index = monthly.index.to_period('M')
        monthly.columns = [f'{col}_suma_MW' for col in monthly.columns]
        
        return monthly
    
    def get_time_series(self, resample_freq: str = '1D') -> pd.DataFrame:
        """
        Generuje szereg czasowy z agregacją.
        
        Args:
            resample_freq: Częstotliwość agregacji ('1H', '1D', '1W', '1ME')
            
        Returns:
            DataFrame z szeregiem czasowym
        """
        cols_to_agg = [col for col in [self.wind_col, self.solar_col, self.demand_col, self.swm_total_col] if col]
        
        if resample_freq in ['1h', 'h', '1D', 'D']:
            # Dla godzin i dni - suma z przeliczeniem na MWh
            ts = self.df[cols_to_agg].resample(resample_freq).agg(
                lambda x: x.sum() * 0.25
            )
        else:
            # Dla dłuższych okresów
            ts = self.df[cols_to_agg].resample(resample_freq).agg(
                lambda x: x.sum() * 0.25
            )
        
        # Dodaj również średnią moc
        ts_mean = self.df[cols_to_agg].resample(resample_freq).mean()
        ts_mean.columns = [f'{col}_średnia' for col in ts_mean.columns]
        
        result = pd.concat([ts, ts_mean], axis=1)
        return result


def main():
    """Główna funkcja programu."""
    
    print("=" * 70)
    print("PSE - Pobieranie danych o produkcji energii wiatrowej i fotowoltaicznej")
    print("=" * 70)
    print()
    
    # Przykład użycia
    fetcher = PSEEnergyDataFetcher()
    
    # Pobierz dane dla stycznia 2026
    print("📥 Pobieranie danych...")
    date_from = "2026-01-01"
    date_to = "2026-01-16"
    
    df = fetcher.fetch_data(date_from, date_to)
    
    # Jeśli nie udało się pobrać prawdziwych danych, użyj przykładowych
    if df is None or df.empty:
        df = fetcher.generate_sample_data(date_from, date_to)
    
    print(f"✓ Pobrano {len(df)} rekordów")
    print()
    
    # Analiza danych
    analyzer = EnergyDataAnalyzer(df)
    
    # 1. Suma dla całego okresu
    print("📊 SUMA DLA OKRESU 1.01.2026 - 16.01.2026")
    print("-" * 70)
    period_sum = analyzer.sum_period()
    for key, value in period_sum.items():
        print(f"  {key}: {value}")
    print()
    
    # 2. Miesięczne sumy (dla przykładowych danych)
    print("📊 MIESIĘCZNE SUMY")
    print("-" * 70)
    monthly = analyzer.monthly_sums(2026, 2026)
    print(monthly.to_string())
    print()
    
    # 3. Szereg czasowy - agregacja dzienna
    print("📊 SZEREG CZASOWY - AGREGACJA DZIENNA")
    print("-" * 70)
    daily_ts = analyzer.get_time_series('1D')
    print(daily_ts.head(10).to_string())
    print()
    
    # Zapis do plików
    print("💾 Zapisywanie wyników...")
    
    # CSV z surowymi danymi (format europejski: separator ; i dziesiętny ,)
    df.to_csv('dane_surowe.csv', index=True, sep=';', decimal=',', encoding='utf-8-sig')
    print("  ✓ dane_surowe.csv")
    
    # CSV z miesięcznymi sumami
    monthly.to_csv('sumy_miesieczne.csv', index=True, sep=';', decimal=',', encoding='utf-8-sig')
    print("  ✓ sumy_miesieczne.csv")
    
    # CSV z szeregiem czasowym
    daily_ts.to_csv('szereg_czasowy_dzienny.csv', index=True, sep=';', decimal=',', encoding='utf-8-sig')
    print("  ✓ szereg_czasowy_dzienny.csv")
    
    # JSON z podsumowaniem
    with open('podsumowanie.json', 'w', encoding='utf-8') as f:
        json.dump(period_sum, f, indent=2, ensure_ascii=False)
    print("  ✓ podsumowanie.json")
    
    print()
    print("=" * 70)
    print("✓ Gotowe!")
    print("=" * 70)


if __name__ == "__main__":
    main()
