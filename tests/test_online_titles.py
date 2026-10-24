import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from inventory.titles import lookup_title,enrich_title,full_title,canonical_isbn

ISBN='9780820421834'


class OnlineTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'.tmp')
        self.cache_patch=patch('inventory.titles.CACHE',Path(self.tmp.name)/'titles.json')
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop()
        self.tmp.cleanup()

    def test_exact_google_isbn_title_subtitle_and_cache(self):
        data={'items':[{'volumeInfo':{'title':'The Beatles with Lacan','subtitle':"Rock ’n’ Roll as Requiem for the Modern Age",
                                     'industryIdentifiers':[{'identifier':'0820421839'}]}}]}
        with patch('inventory.titles.fetch_json',return_value=data) as fetch:
            result=lookup_title(ISBN)
            self.assertEqual(result['title'],"The Beatles with Lacan: Rock ’n’ Roll as Requiem for the Modern Age")
            self.assertEqual(lookup_title(ISBN),result)
            self.assertEqual(fetch.call_count,1)

    def test_wrong_edition_rejected_open_library_fallback(self):
        google={'items':[{'volumeInfo':{'title':'Wrong book','industryIdentifiers':[{'identifier':'9780306406157'}]}}]}
        library={'title':'Correct title','subtitle':'Full subtitle','isbn_13':[ISBN]}
        with patch('inventory.titles.fetch_json',side_effect=[google,library]):
            result=lookup_title(ISBN)
            self.assertEqual(result['title'],'Correct title: Full subtitle')
            self.assertEqual(result['provider'],'Open Library')

    def test_network_failure_retains_local_title(self):
        row={'isbn':ISBN,'title':'Local PDF title','notes':[]}
        with patch('inventory.titles.fetch_json',side_effect=OSError('offline')):
            result=enrich_title(row)
        self.assertEqual(result['title'],'Local PDF title')
        self.assertEqual(row['notes'],[])
        self.assertIsNone(result['title_lookup']['title'])

    def test_wrong_isbn_and_empty_title_never_accepted(self):
        with patch('inventory.titles.fetch_json',side_effect=[{'items':[]},{'title':'Wrong','isbn_13':['9780306406157']}]):
            self.assertIsNone(lookup_title(ISBN)['title'])

    def test_no_duplicate_subtitle(self):
        self.assertEqual(full_title('Book: The whole story','The whole story'),'Book: The whole story')
        self.assertEqual(full_title('Book','Subtitle'),'Book: Subtitle')

    def test_invalid_isbn_and_disabled_lookup_make_no_request(self):
        with patch('inventory.titles.fetch_json',side_effect=AssertionError('No request')):
            self.assertIsNone(lookup_title('NOT FOUND')['title'])
            row={'isbn':ISBN,'title':'Local','notes':[]}
            self.assertIs(enrich_title(row,False),row)
        self.assertEqual(canonical_isbn('0820421839'),ISBN)


if __name__=='__main__':
    unittest.main()
