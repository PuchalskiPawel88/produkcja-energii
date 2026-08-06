#!/usr/bin/env python3
"""
Moduł łączący dane z PSE i ENTSO-E w jeden kompleksowy analizator.
"""

import pandas as pd
from typing import Optional
from datetime import datetime
import json

from pse_energy_scraper import PSEEnergyDataFetcher, EnergyDataAnalyzer
from entsoe_data_fetcher import ENTSOEDataFetcher

# Eksportowane klasy i funkcje
__all__ = [
    'CombinedEnergyDataFetcher',
    'CombinedEnergyDataAnalyzer',
    'validate_data_continuity',
    'print_data_quality_report'
]


class CombinedEnergyDataFetcher:
    """Klasa łącząca dane z PSE i ENTSO-E."""
    
    def __init__(self, entsoe_api_key: Optional[str] = None):
        """
        Inicjalizacja fetcher'a łączącego oba źródła danych.
        
        Args:
            entsoe_api_key: Klucz API ENTSO-E (opcjonalny, może być w .env)
        """
        self.pse_fetcher = PSEEnergyDataFetcher()
        
        try:
            self.entsoe_fetcher = ENTSOEDataFetcher(api_key=entsoe_api_key)
            self.entsoe_available = True
        except ValueError as e:
            print(f"⚠️  ENTSO-E nie jest dostępne: {e}")
            self.entsoe_available = False
    
    def fetch_combined_data(self, date_from: str, date_to: str) -> Optional[pd.DataFrame]:
        """
        Pobiera i łączy dane z PSE i ENTSO-E.
        
        Args:
            date_from: Data początkowa w formacie YYYY-MM-DD
            date_to: Data końcowa w formacie YYYY-MM-DD
            
        Returns:
            DataFrame z połączonymi danymi lub None w przypadku błędu
        """
        print("=" * 70)
        print(f"📊 Pobieranie danych dla okresu {date_from} - {date_to}")
        print("=" * 70)
        print()
        
        # Pobierz dane z PSE
        print("🔌 PSE - Dane rynkowe...")
        df_pse = self.pse_fetcher.fetch_data(date_from, date_to)
        
        if df_pse is None or df_pse.empty:
            print("⚠️  Brak danych z PSE")
            return None
        
        # Pobierz dane z ENTSO-E (jeśli dostępne)
        df_entsoe = None
        if self.entsoe_available:
            print()
            print("⚡ ENTSO-E - Dane o produkcji...")
            df_entsoe = self.entsoe_fetcher.fetch_generation_data(date_from, date_to)
        
        # Połącz dane
        if df_entsoe is not None and not df_entsoe.empty:
            print()
            print("🔗 Łączenie danych PSE + ENTSO-E...")
            
            # Upewnij się że oba DataFrame mają kolumnę Data jako index
            if 'Data' not in df_pse.index.names:
                if 'Data' in df_pse.columns:
                    df_pse.set_index('Data', inplace=True)
            
            if 'Data' not in df_entsoe.index.names:
                if 'Data' in df_entsoe.columns:
                    df_entsoe.set_index('Data', inplace=True)
            
            # Konwertuj strefy czasowe do jednolitego formatu
            # PSE używa czasu lokalnego bez tz, ENTSO-E ma timezone
            
            # Jeśli PSE nie ma tz, dodaj tz lokalną i od razu usuń (dla porównań bez tz)
            if df_pse.index.tz is None:
                # Sprawdź czy mamy markery DST (kolumna _dst_marker)
                if '_dst_marker' in df_pse.columns and (df_pse['_dst_marker'] != '').any():
                    # Użyj markera do określenia czy timestamp jest przed (False) czy po (True) zmianie czasu
                    # 'first' = przed zmianą (DST=True), 'second' = po zmianie (DST=False)
                    ambiguous_array = df_pse['_dst_marker'].map({'first': True, 'second': False, '': 'infer'})
                    df_pse.index = df_pse.index.tz_localize('Europe/Warsaw', ambiguous=ambiguous_array.tolist(), nonexistent='shift_forward')
                    # Usuń kolumnę pomocniczą
                    df_pse.drop(columns=['_dst_marker'], inplace=True)
                else:
                    # Brak markerów DST - użyj 'NaT' dla niejednoznacznych (usuniemy je później)
                    try:
                        df_pse.index = df_pse.index.tz_localize('Europe/Warsaw', ambiguous='infer', nonexistent='shift_forward')
                    except Exception:
                        # Jeśli 'infer' nie działa, użyj 'NaT' aby oznaczyć niejednoznaczne jako brakujące
                        df_pse.index = df_pse.index.tz_localize('Europe/Warsaw', ambiguous='NaT', nonexistent='shift_forward')
                    # Zapisz liczbę rekordów przed usunięciem
                    before_removal = len(df_pse)
                    # Znajdź daty z NaT (to są dni DST)
                    nat_mask = df_pse.index.isna()
                    if nat_mask.any():
                        # Znajdź datę z pierwszym NaT (to jest dzień DST)
                        # Sprawdź kolumnę Data jeśli istnieje
                        if 'Data' in df_pse.columns:
                            problem_dates = df_pse[nat_mask]['Data'].dt.date.unique()
                            problem_date = problem_dates[0].strftime('%Y-%m-%d') if len(problem_dates) > 0 else 'nieznana'
                        else:
                            # Spróbuj znaleźć z sąsiednich timestampów
                            valid_dates = df_pse[~nat_mask].index
                            problem_date = valid_dates.min().strftime('%Y-%m-%d') if len(valid_dates) > 0 else 'nieznana'
                    else:
                        problem_date = 'nieznana'
                    
                    # Usuń wiersze z NaT
                    df_pse = df_pse[df_pse.index.notna()]
                    removed_count = before_removal - len(df_pse)
                    
                    if removed_count > 0:
                        print(f"   ⏰ Dzień zmiany czasu ({problem_date}): usunięto {removed_count} niejednoznaczych pomiarów")
                # Usuń timezone od razu przez konwersję na naive datetime
                df_pse.index = pd.DatetimeIndex(df_pse.index.tz_localize(None))
            
            # Jeśli ENTSO-E ma inną tz, konwertuj do Europe/Warsaw i usuń tz
            if df_entsoe.index.tz is not None:
                df_entsoe.index = df_entsoe.index.tz_convert('Europe/Warsaw')
                # Usuń timezone przez konwersję na naive datetime
                df_entsoe.index = pd.DatetimeIndex(df_entsoe.index.tz_localize(None))
            else:
                # Jeśli nie ma tz, dodaj Europe/Warsaw i usuń
                try:
                    df_entsoe.index = df_entsoe.index.tz_localize('Europe/Warsaw', ambiguous='infer', nonexistent='shift_forward')
                except Exception:
                    df_entsoe.index = df_entsoe.index.tz_localize('Europe/Warsaw', ambiguous='NaT', nonexistent='shift_forward')
                    df_entsoe = df_entsoe[df_entsoe.index.notna()]
                df_entsoe.index = pd.DatetimeIndex(df_entsoe.index.tz_localize(None))
            
            # Filtruj ENTSO-E do tego samego zakresu dat co PSE
            # ENTSO-E może mieć dane wykraczające poza żądany okres (pobranie od poprzedniego dnia)
            if not df_pse.empty:
                min_date = df_pse.index.min()
                max_date = df_pse.index.max()
                df_entsoe = df_entsoe[(df_entsoe.index >= min_date) & (df_entsoe.index <= max_date)]
            
            # WAŻNE: Usuń duplikaty z ENTSO-E PRZED łączeniem
            # ENTSO-E może mieć duplikaty dla tego samego timestampu
            if df_entsoe.index.duplicated().any():
                print(f"   ⚠️  ENTSO-E: wykryto {df_entsoe.index.duplicated().sum()} duplikatów")
                df_entsoe = df_entsoe[~df_entsoe.index.duplicated(keep='first')]
                print(f"   ✓ Usunięto duplikaty ENTSO-E, pozostało {len(df_entsoe)} rekordów")
            
            # Diagnostyka przed merge
            pse_count = len(df_pse)
            entsoe_count = len(df_entsoe)
            common_timestamps = len(df_pse.index.intersection(df_entsoe.index))
            
            # UŻYJ LEFT JOIN zamiast INNER - zachowaj wszystkie timestampy PSE
            # PSE jest głównym źródłem czasu (dokładniejsze, co 15 minut)
            # ENTSO-E może mieć luki lub różne timestampy
            df_combined = pd.merge(
                df_pse,
                df_entsoe,
                left_index=True,
                right_index=True,
                how='left',  # LEFT JOIN - zachowaj wszystkie rekordy PSE
                suffixes=('_PSE', '_ENTSOE')
            )
            
            # Nie wypełniaj NaN zerami - zostaw jako NaN aby średnia była poprawna
            df_combined.reset_index(inplace=True)
            
            # Przetwarzanie dat
            df_combined['Data'] = pd.to_datetime(df_combined['Data'])
            
            # Sprawdź duplikaty przed usunięciem
            duplicates_before = len(df_combined)
            duplicate_timestamps = df_combined['Data'].duplicated().sum()
            
            # USUŃ DUPLIKATY - zachowaj pierwszy wystąpienie
            if duplicate_timestamps > 0:
                print(f"\n⚠️  Wykryto {duplicate_timestamps} zduplikowanych timestampów")
                df_combined = df_combined.drop_duplicates(subset=['Data'], keep='first')
                print(f"   Usunięto {duplicates_before - len(df_combined)} duplikatów")
            
            # Statystyki łączenia
            merged_count = len(df_combined)
            entsoe_matched = df_combined.iloc[:, -1].notna().sum()  # Ostatnia kolumna z ENTSO-E
            
            print(f"✓ Połączono {merged_count} rekordów (po usunięciu duplikatów)")
            print(f"   PSE: {pse_count}, ENTSO-E: {entsoe_count}")
            print(f"   Wspólne timestampy: {common_timestamps}")
            print(f"   Dopasowano ENTSO-E: {entsoe_matched} / {merged_count} ({entsoe_matched/merged_count*100:.1f}%)")
            
            # Walidacja ciągłości danych
            validation = validate_data_continuity(df_combined, date_from, date_to)
            print_data_quality_report(validation)
            
            return df_combined
        else:
            print()
            print("⚠️  Używam tylko danych PSE")
            
            # Walidacja dla samych danych PSE
            validation = validate_data_continuity(df_pse, date_from, date_to)
            print_data_quality_report(validation)
            
            return df_pse


def validate_data_continuity(df: pd.DataFrame, date_from: str, date_to: str, expected_interval_minutes: int = 15) -> dict:
    """
    Sprawdza ciągłość czasową danych i wykrywa brakujące dni/godziny.
    
    Args:
        df: DataFrame z danymi (musi mieć kolumnę 'Data')
        date_from: Oczekiwana data początkowa (YYYY-MM-DD)
        date_to: Oczekiwana data końcowa (YYYY-MM-DD)
        expected_interval_minutes: Oczekiwany interwał czasowy w minutach (domyślnie 15)
        
    Returns:
        Słownik z informacjami o ciągłości:
        - is_complete: bool - czy dane są kompletne
        - expected_records: int - oczekiwana liczba rekordów
        - actual_records: int - faktyczna liczba rekordów
        - missing_records: int - liczba brakujących rekordów
        - missing_days: list - lista dni z niekompletnymi danymi
        - records_per_day: dict - liczba rekordów dla każdego dnia
    """
    from datetime import timedelta
    
    # Oblicz oczekiwaną liczbę rekordów
    start_date = datetime.strptime(date_from, '%Y-%m-%d')
    end_date = datetime.strptime(date_to, '%Y-%m-%d')
    days_count = (end_date - start_date).days + 1
    records_per_day = (24 * 60) // expected_interval_minutes
    expected_total = days_count * records_per_day
    
    actual_total = len(df)
    missing_total = expected_total - actual_total
    
    # Sprawdź liczbę rekordów dla każdego dnia
    df_check = df.copy()
    if 'Data' not in df_check.columns:
        df_check = df_check.reset_index()
    
    df_check['Data'] = pd.to_datetime(df_check['Data'])
    df_check['Date_Only'] = df_check['Data'].dt.date
    
    records_by_day = df_check.groupby('Date_Only').size().to_dict()
    
    # Sprawdź duplikaty
    duplicate_timestamps = df_check['Data'].duplicated().sum()
    duplicate_days = []
    if duplicate_timestamps > 0:
        # Znajdź dni z duplikatami
        df_check['has_duplicate'] = df_check['Data'].duplicated(keep=False)
        days_with_dups = df_check[df_check['has_duplicate']].groupby('Date_Only').size()
        for date, dup_count in days_with_dups.items():
            duplicate_days.append({
                'date': date.strftime('%Y-%m-%d'),
                'duplicate_count': dup_count - records_by_day.get(date, 0)
            })
    
    # Znajdź dni z niekompletnymi danymi
    # Uwaga: dni zmiany czasu mogą mieć 95 (czas letni) lub 97-100 (czas zimowy) rekordów
    missing_days = []
    dst_transition_days = []
    days_with_excess = []  # Dni z nadmiarem danych (>100)
    current_date = start_date.date()
    end_date_only = end_date.date()
    
    while current_date <= end_date_only:
        count = records_by_day.get(current_date, 0)
        
        # Tolerancja dla dni zmiany czasu:
        # - Czas zimowy (październik): 100 rekordów (powtórzona godzina 2)
        # - Czas zimowy z usuniętymi niejednoznacznymi: 92-96 rekordów
        # - Czas letni (marzec): 92 rekordy (przeskoczona godzina 2)
        if 92 <= count <= 100 and count != records_per_day:
            # Prawdopodobnie dzień zmiany czasu
            dst_transition_days.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'expected': records_per_day,
                'actual': count,
                'note': 'Prawdopodobnie dzień zmiany czasu'
            })
        elif count < 92:  # Wyraźnie brakuje danych (nie jest to tylko DST)
            missing_days.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'expected': records_per_day,
                'actual': count,
                'missing': records_per_day - count
            })
        elif count > 100:  # Nadmiar danych (prawdopodobnie duplikaty)
            days_with_excess.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'expected': records_per_day,
                'actual': count,
                'excess': count - records_per_day
            })
        
        current_date += timedelta(days=1)
    
    return {
        'is_complete': missing_total == 0,
        'expected_records': expected_total,
        'actual_records': actual_total,
        'missing_records': missing_total,
        'missing_days': missing_days,
        'dst_transition_days': dst_transition_days,
        'days_with_excess': days_with_excess,
        'duplicate_timestamps': duplicate_timestamps,
        'duplicate_days': duplicate_days,
        'records_per_day': records_by_day,
        'records_per_day_expected': records_per_day,
        'days_count': days_count
    }


def print_data_quality_report(validation_result: dict, save_to_file: Optional[str] = None):
    """
    Wyświetla raport jakości danych.
    
    Args:
        validation_result: Wynik z validate_data_continuity()
        save_to_file: Opcjonalna ścieżka do zapisania raportu w JSON
    """
    print("\n" + "=" * 70)
    print("📋 RAPORT JAKOŚCI DANYCH")
    print("=" * 70)
    
    print(f"\nOczekiwano:     {validation_result['expected_records']:,} rekordów")
    print(f"Pobrano:        {validation_result['actual_records']:,} rekordów")
    print(f"Brakuje:        {validation_result['missing_records']:,} rekordów")
    print(f"Kompletność:    {(validation_result['actual_records'] / validation_result['expected_records'] * 100):.2f}%")
    
    print(f"\nOczekiwano:     {validation_result['records_per_day_expected']} rekordów/dzień")
    print(f"Okres:          {validation_result['days_count']} dni")
    
    # Informacja o duplikatach
    dup_count = validation_result.get('duplicate_timestamps', 0)
    if dup_count > 0:
        print(f"\n🔄 Uwaga: Wykryto i usunięto {dup_count} duplikatów")
        dup_days = validation_result.get('duplicate_days', [])
        if dup_days:
            print(f"   Dni z duplikatami:")
            for day_info in dup_days[:5]:
                print(f"   - {day_info['date']}")
            if len(dup_days) > 5:
                print(f"   ... i {len(dup_days) - 5} więcej")
    
    # Informacja o dniach zmiany czasu
    dst_days = validation_result.get('dst_transition_days', [])
    if dst_days:
        print(f"\n⏰ DZIEŃ ZMIANY CZASU - wykryto {len(dst_days)} dni:")
        print("-" * 70)
        for day_info in dst_days:
            actual = day_info['actual']
            expected = day_info['expected']
            missing = expected - actual
            
            # Rozpoznaj typ zmiany czasu na podstawie miesiąca
            date_str = day_info['date']
            month = int(date_str.split('-')[1])
            
            if month == 3:  # Marzec - zmiana na czas letni
                change_type = "CZAS LETNI (marzec)"
                explanation = f"Brak {abs(missing)} pomiarów z godziny 2:00-2:45 (zegar 2→3)"
            elif month == 10:  # Październik - zmiana na czas zimowy
                if actual < expected:
                    # Usunięto niejednoznaczne timestampy
                    change_type = "CZAS ZIMOWY (październik)"
                    explanation = f"Brak {abs(missing)} pomiarów z powtórzonej godziny 2:00-2:45 (zegar 3→2)"
                else:
                    # Zachowano powtórzoną godzinę
                    change_type = "CZAS ZIMOWY (październik)"
                    explanation = f"Powtórzona godzina 2:00-3:00 - {actual - expected} dodatkowych pomiarów (zegar 3→2)"
            else:
                change_type = "nieznany typ"
                if actual < expected:
                    explanation = f"Brak {abs(missing)} pomiarów"
                else:
                    explanation = f"{actual - expected} dodatkowych pomiarów"
            
            print(f"\n📅 {day_info['date']}")
            print(f"   Typ zmiany: {change_type}")
            print(f"   Pomiary: {actual} z {expected} oczekiwanych")
            print(f"   {explanation}")
            print(f"   ℹ️  To normalne - nie jest błędem systemu")
        print("-" * 70)
    
    # Informacja o dniach z nadmiarem danych (po usunięciu duplikatów - jeśli nadal są)
    excess_days = validation_result.get('days_with_excess', [])
    if excess_days:
        print(f"\n⚠️  Wykryto {len(excess_days)} dni z nadmiarem danych:")
        for day_info in excess_days[:5]:
            print(f"   {day_info['date']}: {day_info['actual']} rekordów (+{day_info['excess']})")
        if len(excess_days) > 5:
            print(f"   ... i {len(excess_days) - 5} więcej")
    
    if validation_result['is_complete'] and not validation_result['missing_days'] and not excess_days:
        print("\n✅ Dane są kompletne!")
    else:
        missing_days = validation_result['missing_days']
        if missing_days:
            # Sprawdź które z brakujących dni to dni DST
            dst_dates = {d['date'] for d in validation_result.get('dst_transition_days', [])}
            
            print(f"\n⚠️  Wykryto {len(missing_days)} dni z niekompletnymi danymi:")
            print("\n" + "-" * 70)
            print(f"{'Data':<12} {'Oczekiwano':<12} {'Pobrano':<12} {'Brakuje':<12} {'Uwaga':<20}")
            print("-" * 70)
            
            # Pokaż maksymalnie 20 dni, resztę zsumuj
            display_limit = 20
            for i, day_info in enumerate(missing_days[:display_limit]):
                note = ""
                if day_info['date'] in dst_dates:
                    note = "⏰ Zmiana czasu"
                print(f"{day_info['date']:<12} {day_info['expected']:<12} {day_info['actual']:<12} {day_info['missing']:<12} {note:<20}")
            
            if len(missing_days) > display_limit:
                remaining = len(missing_days) - display_limit
                total_missing_in_remaining = sum(d['missing'] for d in missing_days[display_limit:])
                print(f"... i jeszcze {remaining} dni (brakuje łącznie {total_missing_in_remaining} rekordów)")
            
            print("-" * 70)
    
    # Zapis do pliku jeśli podano ścieżkę
    if save_to_file:
        try:
            with open(save_to_file, 'w', encoding='utf-8') as f:
                json.dump(validation_result, f, indent=2, ensure_ascii=False, default=str)
            print(f"\n💾 Raport zapisano do: {save_to_file}")
        except Exception as e:
            print(f"\n⚠️  Nie udało się zapisać raportu: {e}")
    
    print()


class CombinedEnergyDataAnalyzer:
    """Klasa do analizy połączonych danych z PSE i ENTSO-E."""
    
    def __init__(self, df: pd.DataFrame):
        """
        Inicjalizacja analizatora.
        
        Args:
            df: DataFrame z połączonymi danymi
        """
        self.df = df.copy()
        self._prepare_data()
    
    def _prepare_data(self):
        """Przygotowuje dane do analizy."""
        # Znajdź kolumnę z datą
        date_columns = [col for col in self.df.columns if 'data' in col.lower() or 'date' in col.lower()]
        
        if date_columns:
            self.df['Data'] = pd.to_datetime(self.df[date_columns[0]])
        elif 'Data' not in self.df.columns:
            print("⚠️  Nie znaleziono kolumny z datą")
            return
        
        self.df.set_index('Data', inplace=True)
        
        # Znajdź dostępne kolumny
        self.available_columns = {
            'wiatr_pse': self._find_column(['sumaryczna generacja źródeł wiatrowych', 'wiatr']),
            'pv_pse': self._find_column(['sumaryczna generacja źródeł fotowoltaicznych', 'fotowoltai']),
            'demand': self._find_column(['zapotrzebowanie']),
            'swm_total': self._find_column(['krajowe saldo wymiany międzysystemowej [mw]']),
            'hard_coal': self._find_column(['węgiel kamienny']),
            'lignite': self._find_column(['węgiel brunatny']),
            'gas': self._find_column(['gaz [mw]']),
            'wind_entsoe': self._find_column(['wiatr [mw]', 'wiatr onshore', 'wiatr lądowy']),
            'wind_onshore_entsoe': self._find_column(['wiatr onshore', 'wiatr lądowy']),
            'wind_offshore_entsoe': self._find_column(['wiatr offshore']),
            'solar_entsoe': self._find_column(['słońce [mw]']),
            'hydro': self._find_column(['woda [mw]']),
            'storage': self._find_column(['magazyny energii']),
            'biomass': self._find_column(['biomasa'])
        }
    
    def _find_column(self, keywords: list) -> Optional[str]:
        """Znajduje kolumnę zawierającą którekolwiek ze słów kluczowych."""
        for col in self.df.columns:
            col_lower = col.lower()
            if any(keyword.lower() in col_lower for keyword in keywords):
                return col
        return None
    
    def sum_period(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
        """
        Sumuje wszystkie dostępne wskaźniki dla podanego okresu.
        
        Args:
            date_from: Data początkowa (opcjonalna)
            date_to: Data końcowa (opcjonalna)
            
        Returns:
            Słownik z sumami dla wszystkich wskaźników
        """
        df_filtered = self.df
        
        if date_from:
            df_filtered = df_filtered[df_filtered.index >= date_from]
        if date_to:
            df_filtered = df_filtered[df_filtered.index <= date_to]
        
        if df_filtered.empty:
            return {'błąd': 'Brak danych dla podanego okresu'}
        
        results = {
            'okres_od': df_filtered.index.min().strftime('%Y-%m-%d %H:%M'),
            'okres_do': df_filtered.index.max().strftime('%Y-%m-%d %H:%M'),
            'liczba_pomiarów': len(df_filtered),
        }
        
        # Dodaj sumy dla wszystkich dostępnych wskaźników
        # Dane są co 15 min, więc mnożymy przez 0.25h aby uzyskać MWh
        for name, col in self.available_columns.items():
            if col and col in df_filtered.columns:
                sum_mw = df_filtered[col].sum()
                mwh = sum_mw * 0.25
                mean_mw = df_filtered[col].mean()
                
                results[f'{name}_suma_MW'] = round(sum_mw, 2)
                results[f'{name}_MWh'] = round(mwh, 2)
                results[f'{name}_średnia_MW'] = round(mean_mw, 2)
        
        return results
    
    def get_time_series(self, resample_freq: str = '1D') -> pd.DataFrame:
        """
        Generuje szereg czasowy z agregacją dla wszystkich wskaźników.
        
        Args:
            resample_freq: Częstotliwość agregacji ('1H', '1D', '1W', '1ME')
            
        Returns:
            DataFrame z szeregiem czasowym
        """
        # Pobierz wszystkie dostępne kolumny numeryczne
        cols_to_agg = [col for col in self.available_columns.values() if col and col in self.df.columns]
        
        if not cols_to_agg:
            return pd.DataFrame()
        
        # Suma z przeliczeniem na MWh
        ts = self.df[cols_to_agg].resample(resample_freq).agg(
            lambda x: x.sum() * 0.25
        )
        
        # Dodaj również średnią moc
        ts_mean = self.df[cols_to_agg].resample(resample_freq).mean()
        ts_mean.columns = [f'{col}_średnia' for col in ts_mean.columns]
        
        result = pd.concat([ts, ts_mean], axis=1)
        return result
    
    def monthly_sums(self, year_from: int, year_to: int) -> pd.DataFrame:
        """
        Generuje miesięczne sumy dla wybranych lat.
        
        Args:
            year_from: Rok początkowy
            year_to: Rok końcowy
            
        Returns:
            DataFrame z miesięcznymi sumami
        """
        # Filtruj dane dla wybranych lat
        df_filtered = self.df[
            (self.df.index.year >= year_from) & 
            (self.df.index.year <= year_to)
        ]
        
        # Pobierz wszystkie dostępne kolumny numeryczne
        cols_to_agg = [col for col in self.available_columns.values() if col and col in df_filtered.columns]
        
        if not cols_to_agg:
            return pd.DataFrame()
        
        # Grupuj po miesiącach i sumuj (konwersja MW -> MWh poprzez * 0.25)
        monthly = df_filtered[cols_to_agg].resample('1ME').agg(
            lambda x: x.sum() * 0.25
        )
        
        # Formatuj kolumny z jednostkami
        monthly.columns = [f'{col}_suma_MW' for col in monthly.columns]
        
        return monthly
    
    def export_to_csv(self, filename: str):
        """Eksportuje dane do CSV (format europejski)."""
        self.df.to_csv(filename, sep=';', decimal=',', encoding='utf-8-sig')
        print(f"💾 Zapisano: {filename}")
    
    def export_to_json(self, filename: str):
        """Eksportuje dane do JSON."""
        self.df.reset_index().to_json(filename, orient='records', date_format='iso', force_ascii=False, indent=2)
        print(f"💾 Zapisano: {filename}")


def main():
    """Funkcja testowa."""
    print("=" * 70)
    print("Combined Energy Data Fetcher - Test")
    print("=" * 70)
    print()
    
    fetcher = CombinedEnergyDataFetcher()
    df = fetcher.fetch_combined_data('2025-01-15', '2025-01-15')
    
    if df is not None:
        print()
        print("📊 Dostępne kolumny:")
        print("-" * 70)
        for col in df.columns:
            print(f"  - {col}")
        
        print()
        print("📊 Przykładowe dane (pierwsze 5 rekordów):")
        print("-" * 70)
        print(df.head(5).to_string())
        
        # Analiza
        analyzer = CombinedEnergyDataAnalyzer(df)
        results = analyzer.sum_period()
        
        print()
        print("📈 Podsumowanie:")
        print("-" * 70)
        for key, value in results.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
