import unittest

from entsoe_data_fetcher import ENTSOEDataFetcher


class EntsoeWindSplitTest(unittest.TestCase):
    def test_parse_xml_separates_offshore_and_onshore_wind(self):
        fetcher = ENTSOEDataFetcher.__new__(ENTSOEDataFetcher)

        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <GenerationDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
          <TimeSeries>
            <MktPSRType>
              <psrType>B18</psrType>
            </MktPSRType>
            <Period>
              <timeInterval>
                <start>2026-01-01T00:00:00Z</start>
                <end>2026-01-01T01:00:00Z</end>
              </timeInterval>
              <resolution>PT60M</resolution>
              <Point>
                <position>1</position>
                <quantity>12.5</quantity>
              </Point>
            </Period>
          </TimeSeries>
          <TimeSeries>
            <MktPSRType>
              <psrType>B19</psrType>
            </MktPSRType>
            <Period>
              <timeInterval>
                <start>2026-01-01T00:00:00Z</start>
                <end>2026-01-01T01:00:00Z</end>
              </timeInterval>
              <resolution>PT60M</resolution>
              <Point>
                <position>1</position>
                <quantity>7.5</quantity>
              </Point>
            </Period>
          </TimeSeries>
        </GenerationDocument>
        """

        df = fetcher._parse_xml_response(xml_content, '2026-01-01', '2026-01-01')

        self.assertIsNotNone(df)
        self.assertIn('Wiatr offshore [MW]', df.columns)
        self.assertIn('Wiatr onshore [MW]', df.columns)
        self.assertEqual(df['Wiatr offshore [MW]'].iloc[0], 12.5)
        self.assertEqual(df['Wiatr onshore [MW]'].iloc[0], 7.5)


if __name__ == '__main__':
    unittest.main()
