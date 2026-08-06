#!/usr/bin/env python3
"""
Interaktywny skrypt do pobierania i analizowania danych PSE o produkcji energii.
"""

import sys
import os
from datetime import datetime

# Dodaj ścieżkę do src jeśli uruchamiamy z głównego folderu
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from pse_energy_scraper import PSEEnergyDataFetcher, EnergyDataAnalyzer
import json

# Spróbuj zaimportować moduły ENTSO-E (opcjonalne)
try:
    from combined_energy_data import (
        CombinedEnergyDataFetcher, 
        CombinedEnergyDataAnalyzer,
        validate_data_continuity,
        print_data_quality_report
    )
    ENTSOE_AVAILABLE = True
except ImportError:
    ENTSOE_AVAILABLE = False

# Sprawdź czy klucz ENTSO-E jest dostępny
ENTSOE_API_KEY = os.getenv("ENTSOE_API_KEY")
if ENTSOE_AVAILABLE and not ENTSOE_API_KEY:
    ENTSOE_AVAILABLE = False


def print_menu():
    """Wyświetla menu główne."""
    print("\n" + "=" * 70)
    print("PSE + ENTSO-E - Analiza produkcji energii")
    if ENTSOE_AVAILABLE and ENTSOE_API_KEY:
        print("✓ Tryb FULL: PSE + ENTSO-E (wszystkie źródła)")
        print("  Dostępne: węgiel, gaz, woda, biomasa, magazyny energii")
    else:
        print("⚠  Tryb PSE: tylko podstawowe dane (wiatr, słońce, zapotrzebowanie)")
        print("")
        print("  Aby włączyć pełny tryb z ENTSO-E:")
        print("  1. Zarejestruj się na: https://transparency.entsoe.eu/")
        print("  2. Wygeneruj klucz API w ustawieniach konta")
        print("  3. Skopiuj .env.example na .env i wpisz klucz")
        print("     lub uruchom: ./run.sh setup")
        print("  4. Uruchom ponownie ten skrypt")
    print("=" * 70)
    print("\nWybierz opcję:")
    print("  1. Suma dla wybranego okresu")
    print("  2. Miesięczne sumy od 2020 do teraz")
    print("  3. Szereg czasowy z wybraną agregacją")
    print("  4. Pełna analiza i eksport danych")
    print("  0. Wyjście")
    print()


def get_date_input(prompt: str, default: str = None) -> str:
    """
    Pobiera datę od użytkownika.
    
    Args:
        prompt: Tekst zachęty
        default: Wartość domyślna
        
    Returns:
        Data w formacie YYYY-MM-DD
    """
    while True:
        if default:
            user_input = input(f"{prompt} [{default}]: ").strip()
            if not user_input:
                return default
        else:
            user_input = input(f"{prompt}: ").strip()
        
        # Akceptuj różne formaty
        for fmt in ['%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y', '%Y/%m/%d']:
            try:
                dt = datetime.strptime(user_input, fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue
        
        print("❌ Nieprawidłowy format daty. Użyj formatu: YYYY-MM-DD lub DD.MM.YYYY")


def option_period_sum():
    """Opcja 1: Suma dla wybranego okresu."""
    print("\n📊 SUMA DLA WYBRANEGO OKRESU")
    print("-" * 70)
    
    date_from = get_date_input("Podaj datę początkową (YYYY-MM-DD lub DD.MM.YYYY)", "2026-01-01")
    date_to = get_date_input("Podaj datę końcową", "2026-01-16")
    
    # Walidacja dat
    from datetime import datetime
    try:
        df = datetime.strptime(date_from, '%Y-%m-%d')
        dt = datetime.strptime(date_to, '%Y-%m-%d')
        if df > dt:
            print("\n⚠️  BŁĄD: Data początkowa jest późniejsza niż końcowa!")
            print(f"   Początek: {date_from}")
            print(f"   Koniec: {date_to}")
            return
    except Exception as e:
        print(f"\n⚠️  BŁĄD walidacji dat: {e}")
        return
    
    # Tryb combined (PSE + ENTSO-E) lub tylko PSE
    use_combined = ENTSOE_AVAILABLE and ENTSOE_API_KEY
    
    if use_combined:
        print(f"\n📥 Pobieranie danych dla okresu {date_from} do {date_to}...")
        print("   Tryb: PSE + ENTSO-E (pełne dane)")
    else:
        print(f"\n📥 Pobieranie danych dla okresu {date_from} do {date_to}...")
        print("   Tryb: tylko PSE (podstawowe dane)")
    
    df = None
    fetcher = None
    analyzer_class = None
    
    if use_combined:
        try:
            fetcher = CombinedEnergyDataFetcher()
            df = fetcher.fetch_combined_data(date_from, date_to)
            analyzer_class = CombinedEnergyDataAnalyzer
        except Exception as e:
            print(f"⚠️  Błąd trybu combined: {e}")
            print("   Używam tylko danych PSE")
            use_combined = False
            df = None
    
    if not use_combined:
        fetcher = PSEEnergyDataFetcher()
        df = fetcher.fetch_data(date_from, date_to)
        analyzer_class = EnergyDataAnalyzer
    
    # Sprawdź czy udało się pobrać dane
    if df is None or (hasattr(df, 'empty') and df.empty):
        print("⚠️  Nie udało się pobrać danych z API, używam przykładowych danych")
        # Upewnij się że mamy PSEEnergyDataFetcher do generowania przykładowych danych
        if not isinstance(fetcher, PSEEnergyDataFetcher):
            fetcher = PSEEnergyDataFetcher()
            analyzer_class = EnergyDataAnalyzer
        df = fetcher.generate_sample_data(date_from, date_to)
        
        # Sprawdź czy generowanie przykładowych danych się powiodło
        if df is None or df.empty:
            print("\n❌ Błąd: Nie udało się wygenerować danych")
            print("Naciśnij Enter aby kontynuować...")
            input()
            return
    
    print(f"✓ Pobrano {len(df)} rekordów\n")
    
    analyzer = analyzer_class(df)
    results = analyzer.sum_period()
    
    # Sprawdź czy są błędy
    if 'błąd' in results:
        print(f"\n⚠️  {results['błąd']}")
        return
    
    print("\n📈 WYNIKI:")
    print("-" * 70)
    print(f"Okres:              {results.get('okres_od')} - {results.get('okres_do')}")
    print(f"Liczba pomiarów:    {results.get('liczba_pomiarów', 0)}")
    
    # Wyświetl wszystkie dostępne wskaźniki
    # Najpierw sprawdź czy to tryb combined czy podstawowy
    is_combined = 'wiatr_pse_suma_MW' in results
    
    if is_combined:
        categories = {
            'WIATR (PSE)': 'wiatr_pse',
            'FOTOWOLTAIKA (PSE)': 'pv_pse',
            'ZAPOTRZEBOWANIE': 'demand',
            'SALDO WYMIANY': 'swm_total',
            'WĘGIEL KAMIENNY': 'hard_coal',
            'WĘGIEL BRUNATNY': 'lignite',
            'GAZ': 'gas',
            'WIATR (ENTSO-E, ONSHORE)': 'wind_onshore_entsoe',
            'WIATR (ENTSO-E, OFFSHORE)': 'wind_offshore_entsoe',
            'SŁOŃCE (ENTSO-E)': 'solar_entsoe',
            'WODA': 'hydro',
            'MAGAZYNY ENERGII': 'storage',
            'BIOMASA': 'biomass'
        }
    else:
        # Tryb podstawowy PSE
        categories = {
            'WIATR': 'wiatr',
            'FOTOWOLTAIKA': 'fotowoltaika',
            'ZAPOTRZEBOWANIE': 'zapotrzebowanie',
            'SALDO WYMIANY': 'saldo_wymiany'
        }
    
    for category_name, key_prefix in categories.items():
        suma_key = f'{key_prefix}_suma_MW'
        mwh_key = f'{key_prefix}_MWh'
        avg_key = f'{key_prefix}_średnia_MW'
        
        if suma_key in results and results.get(suma_key, 0) != 0:
            print()
            print(f"{category_name}:")
            print(f"  Suma MW:          {results.get(suma_key, 0):,.2f} MW")
            print(f"  Energia:          {results.get(mwh_key, 0):,.2f} MWh")
            print(f"  Średnia:          {results.get(avg_key, 0):,.2f} MW")
    
    # Podsumowanie OZE (jeśli są dane)
    if is_combined:
        wiatr_sum = results.get('wiatr_pse_suma_MW', 0)
        pv_sum = results.get('pv_pse_suma_MW', 0)
        wiatr_mwh = results.get('wiatr_pse_MWh', 0)
        pv_mwh = results.get('pv_pse_MWh', 0)
    else:
        wiatr_sum = results.get('wiatr_suma_MW', 0)
        pv_sum = results.get('fotowoltaika_suma_MW', 0)
        wiatr_mwh = results.get('wiatr_MWh', 0)
        pv_mwh = results.get('fotowoltaika_MWh', 0)
    
    if wiatr_sum or pv_sum:
        print()
        print("RAZEM OZE (WIATR + FOTOWOLTAIKA):")
        print(f"  Suma MW:          {wiatr_sum + pv_sum:,.2f} MW")
        print(f"  Energia:          {wiatr_mwh + pv_mwh:,.2f} MWh")
    
    # Opcja zapisu
    save = input("\n💾 Czy zapisać wyniki do pliku? (t/n): ").strip().lower()
    if save == 't':
        filename = f"suma_{date_from}_{date_to}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"✓ Zapisano do {filename}")


def option_monthly_sums():
    """Opcja 2: Miesięczne sumy."""
    print("\n📊 MIESIĘCZNE SUMY")
    print("-" * 70)
    
    year_from = input("Podaj rok początkowy [2020]: ").strip()
    year_from = int(year_from) if year_from else 2020
    
    year_to = input(f"Podaj rok końcowy [{datetime.now().year}]: ").strip()
    year_to = int(year_to) if year_to else datetime.now().year
    
    # Pobierz dane dla całego okresu
    date_from = f"{year_from}-01-01"
    date_to = f"{year_to}-12-31"
    
    # Tryb combined (PSE + ENTSO-E) lub tylko PSE
    use_combined = ENTSOE_AVAILABLE and ENTSOE_API_KEY
    
    print(f"\n📥 Pobieranie danych dla lat {year_from}-{year_to}...")
    if use_combined:
        print("   Tryb: PSE + ENTSO-E (pełne dane)")
    else:
        print("   Tryb: tylko PSE (podstawowe dane)")
    print("⚠️  Uwaga: Pobieranie danych dla wielu lat może zająć trochę czasu...")
    if use_combined:
        try:
            fetcher = CombinedEnergyDataFetcher()
            df = fetcher.fetch_combined_data(date_from, date_to)
            analyzer_class = CombinedEnergyDataAnalyzer
        except Exception as e:
            print(f"⚠️  Błąd trybu combined: {e}")
            print("   Używam tylko danych PSE")
            use_combined = False
    
    if not use_combined:
        fetcher = PSEEnergyDataFetcher()
        df = fetcher.fetch_data(date_from, date_to)
        analyzer_class = EnergyDataAnalyzer
    
    if df is None or df.empty:
        print("⚠️  Nie udało się pobrać danych z API, używam przykładowych danych")
        # Dla przykładu generujemy tylko dla roku 2026
        if not use_combined:
            df = fetcher.generate_sample_data("2026-01-01", "2026-12-31")
    
    print(f"✓ Pobrano {len(df)} rekordów\n")
    
    analyzer = analyzer_class(df)
    monthly = analyzer.monthly_sums(year_from, year_to)
    
    print("\n📈 MIESIĘCZNE SUMY (MW):")
    print("-" * 70)
    print(monthly.to_string())
    
    # Opcja zapisu
    save = input("\n💾 Czy zapisać wyniki do pliku CSV? (t/n): ").strip().lower()
    if save == 't':
        filename = f"miesieczne_sumy_{year_from}_{year_to}.csv"
        monthly.to_csv(filename, sep=';', decimal=',', encoding='utf-8-sig')
        print(f"✓ Zapisano do {filename}")


def option_time_series():
    """Opcja 3: Szereg czasowy."""
    print("\n📊 SZEREG CZASOWY")
    print("-" * 70)
    
    date_from = get_date_input("Podaj datę początkową", "2026-01-01")
    date_to = get_date_input("Podaj datę końcową", "2026-01-31")
    
    # Walidacja dat
    from datetime import datetime
    try:
        df_date = datetime.strptime(date_from, '%Y-%m-%d')
        dt_date = datetime.strptime(date_to, '%Y-%m-%d')
        if df_date > dt_date:
            print("\n⚠️  BŁĄD: Data początkowa jest późniejsza niż końcowa!")
            return
    except Exception:
        pass
    
    print("\nWybierz agregację:")
    print("  1. Co godzinę (1H)")
    print("  2. Co dzień (1D)")
    print("  3. Co tydzień (1W)")
    print("  4. Co miesiąc (1M)")
    print("  Lub wpisz: H, D, W, M")
    
    agg_choice = input("Wybór [2]: ").strip().upper()
    agg_map = {
        '1': '1H', 'H': '1H',
        '2': '1D', 'D': '1D',
        '3': '1W', 'W': '1W',
        '4': '1ME', 'M': '1ME'
    }
    agg_freq = agg_map.get(agg_choice, '1D')
    
    # Tryb combined (PSE + ENTSO-E) lub tylko PSE
    use_combined = ENTSOE_AVAILABLE and ENTSOE_API_KEY
    
    if use_combined:
        print(f"\n📥 Pobieranie danych dla okresu {date_from} do {date_to}...")
        print("   Tryb: PSE + ENTSO-E (pełne dane)")
    else:
        print(f"\n📥 Pobieranie danych dla okresu {date_from} do {date_to}...")
        print("   Tryb: tylko PSE (podstawowe dane)")
    
    df = None
    fetcher = None
    analyzer_class = None
    
    if use_combined:
        try:
            fetcher = CombinedEnergyDataFetcher()
            df = fetcher.fetch_combined_data(date_from, date_to)
            analyzer_class = CombinedEnergyDataAnalyzer
        except Exception as e:
            print(f"⚠️  Błąd trybu combined: {e}")
            print("   Używam tylko danych PSE")
            use_combined = False
            df = None
    
    if not use_combined:
        fetcher = PSEEnergyDataFetcher()
        df = fetcher.fetch_data(date_from, date_to)
        analyzer_class = EnergyDataAnalyzer
    
    if df is None or (hasattr(df, 'empty') and df.empty):
        print("⚠️  Nie udało się pobrać danych z API, używam przykładowych danych")
        if not isinstance(fetcher, PSEEnergyDataFetcher):
            fetcher = PSEEnergyDataFetcher()
            analyzer_class = EnergyDataAnalyzer
        df = fetcher.generate_sample_data(date_from, date_to)
        
        if df is None or df.empty:
            print("\n❌ Błąd: Nie udało się wygenerować danych")
            print("Naciśnij Enter aby kontynuować...")
            input()
            return
    
    print(f"✓ Pobrano {len(df)} rekordów\n")
    
    analyzer = analyzer_class(df)
    ts = analyzer.get_time_series(agg_freq)
    
    print(f"\n📈 SZEREG CZASOWY (agregacja: {agg_freq}):")
    print("-" * 70)
    print(ts.to_string())
    
    # Opcja zapisu
    save = input("\n💾 Czy zapisać szereg czasowy do pliku CSV? (t/n): ").strip().lower()
    if save == 't':
        filename = f"szereg_czasowy_{date_from}_{date_to}_{agg_freq}.csv"
        ts.to_csv(filename, sep=';', decimal=',', encoding='utf-8-sig')
        print(f"✓ Zapisano do {filename}")


def option_full_analysis():
    """Opcja 4: Pełna analiza."""
    print("\n📊 PEŁNA ANALIZA I EKSPORT")
    print("-" * 70)
    
    date_from = get_date_input("Podaj datę początkową", "2026-01-01")
    date_to = get_date_input("Podaj datę końcową", "2026-01-31")
    
    # Walidacja dat
    from datetime import datetime
    try:
        df_date = datetime.strptime(date_from, '%Y-%m-%d')
        dt_date = datetime.strptime(date_to, '%Y-%m-%d')
        if df_date > dt_date:
            print("\n⚠️  BŁĄD: Data początkowa jest późniejsza niż końcowa!")
            return
    except Exception:
        pass
    
    # Tryb combined (PSE + ENTSO-E) lub tylko PSE
    use_combined = ENTSOE_AVAILABLE and ENTSOE_API_KEY
    
    if use_combined:
        print(f"\n📥 Pobieranie danych dla okresu {date_from} do {date_to}...")
        print("   Tryb: PSE + ENTSO-E (pełne dane)")
    else:
        print(f"\n📥 Pobieranie danych dla okresu {date_from} do {date_to}...")
        print("   Tryb: tylko PSE (podstawowe dane)")
    
    df = None
    fetcher = None
    analyzer_class = None
    
    if use_combined:
        try:
            fetcher = CombinedEnergyDataFetcher()
            df = fetcher.fetch_combined_data(date_from, date_to)
            analyzer_class = CombinedEnergyDataAnalyzer
        except Exception as e:
            print(f"⚠️  Błąd trybu combined: {e}")
            print("   Używam tylko danych PSE")
            use_combined = False
            df = None
    
    if not use_combined:
        fetcher = PSEEnergyDataFetcher()
        df = fetcher.fetch_data(date_from, date_to)
        analyzer_class = EnergyDataAnalyzer
    
    if df is None or (hasattr(df, 'empty') and df.empty):
        print("⚠️  Nie udało się pobrać danych z API, używam przykładowych danych")
        if not isinstance(fetcher, PSEEnergyDataFetcher):
            fetcher = PSEEnergyDataFetcher()
            analyzer_class = EnergyDataAnalyzer
        df = fetcher.generate_sample_data(date_from, date_to)
        
        if df is None or df.empty:
            print("\n❌ Błąd: Nie udało się wygenerować danych")
            print("Naciśnij Enter aby kontynuować...")
            input()
            return
    
    print(f"✓ Pobrano {len(df)} rekordów\n")
    
    analyzer = analyzer_class(df)
    
    # 1. Podsumowanie okresu
    print("📈 PODSUMOWANIE OKRESU:")
    print("-" * 70)
    period_sum = analyzer.sum_period()
    for key, value in period_sum.items():
        print(f"  {key:30s}: {value}")
    
    # 2. Szereg czasowy dzienny
    print("\n📈 PRZYKŁADOWE DANE (pierwsze 10 dni):")
    print("-" * 70)
    daily = analyzer.get_time_series('1D')
    print(daily.head(10).to_string())
    
    # Zapis wszystkich plików (format europejski: separator ; i dziesiętny ,)
    print("\n💾 Zapisywanie plików...")
    prefix = f"analiza_{date_from}_{date_to}"
    
    df.to_csv(f'{prefix}_dane_surowe.csv', sep=';', decimal=',', encoding='utf-8-sig')
    print(f"  ✓ {prefix}_dane_surowe.csv")
    
    daily.to_csv(f'{prefix}_dzienny.csv', sep=';', decimal=',', encoding='utf-8-sig')
    print(f"  ✓ {prefix}_dzienny.csv")
    
    hourly = analyzer.get_time_series('1H')
    hourly.to_csv(f'{prefix}_godzinowy.csv', sep=';', decimal=',', encoding='utf-8-sig')
    print(f"  ✓ {prefix}_godzinowy.csv")
    
    with open(f'{prefix}_podsumowanie.json', 'w', encoding='utf-8') as f:
        json.dump(period_sum, f, indent=2, ensure_ascii=False)
    print(f"  ✓ {prefix}_podsumowanie.json")
    
    print("\n✓ Pełna analiza zakończona!")


def main():
    """Główna pętla programu."""
    
    while True:
        print_menu()
        
        choice = input("Wybór: ").strip()
        
        if choice == '0':
            print("\n👋 Do widzenia!")
            sys.exit(0)
        elif choice == '1':
            option_period_sum()
        elif choice == '2':
            option_monthly_sums()
        elif choice == '3':
            option_time_series()
        elif choice == '4':
            option_full_analysis()
        else:
            print("❌ Nieprawidłowy wybór. Spróbuj ponownie.")
        
        input("\n⏎ Naciśnij Enter aby kontynuować...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Przerwano przez użytkownika. Do widzenia!")
        sys.exit(0)
