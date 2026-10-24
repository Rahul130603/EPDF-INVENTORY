import unittest
import numpy as np
from inventory.metadata import language,language_evidence
from inventory.vision import merge_picture_boxes,picture_regions,join_ocr_split_pictures,remove_ocr_text_residue

ENGLISH = 'This book explores the history of the modern world. The reader will find examples and explanations in each of the following chapters. '
GERMAN = 'Dieses Buch beschreibt die Geschichte der Menschen und ihre Beziehung zur Natur. Die verschiedenen Kapitel enthalten zahlreiche Beispiele und wichtige Informationen über das Leben. '
CIP = 'Library of Congress Cataloging-in-Publication Data\n' + ENGLISH*20 + '\nAll rights reserved. Printed in the United States of America.'
SERIES = 'Studies on Themes and Motifs in Literature\nGeneral Editor\nVol. 13\nPETER LANG\nNew York + Washington + Berlin + Paris'


class DetectionRegressions(unittest.TestCase):
    def test_german_not_overridden_by_long_english_copyright_and_series_pages(self):
        pages=['August Strindberg und\ndas expressionistische Stationendrama',SERIES,
               'August Strindberg und das expressionistische Stationendrama\nEine Formstudie',CIP,GERMAN]
        self.assertEqual(language(pages)[0],'GERMAN')

    def test_french_title_not_forced_to_english_by_catalog_text(self):
        pages=['Georges Perec:\nécrire pour ne pas dire',
               'Currents in Comparative Romance Languages and Literatures\nGeneral Editors\nPETER LANG',
               'Stella Béhar\nGeorges Perec:\nécrire pour ne pas dire',CIP,'A ma mère,\nà Nora et Romain.']
        result=language_evidence(pages)
        self.assertIn(result['language'],('FRENCH','NEEDS REVIEW'))
        self.assertEqual(result['detected'],'FRENCH')

    def test_actual_language_names_are_returned_for_multilingual_books(self):
        samples={
            'FRENCH':"Ce livre présente une étude de la littérature française et de son histoire. Les différents chapitres expliquent les idées de l'auteur avec des exemples détaillés et des analyses de la société. ",
            'SPANISH':"Este libro presenta un estudio de la literatura española y de su historia. Los diferentes capítulos explican las ideas del autor con ejemplos detallados y análisis de la sociedad. ",
            'ITALIAN':"Questo libro presenta uno studio della letteratura italiana e della sua storia. I diversi capitoli spiegano le idee dell'autore con esempi dettagliati e analisi della società. "}
        for expected,text in samples.items():
            with self.subTest(language=expected):
                self.assertEqual(language([text*3])[0],expected)

    def test_english_not_overridden_by_german_catalog_text(self):
        self.assertEqual(language([ENGLISH,'Die Deutsche Bibliothek-CIP-Einheitsaufnahme\n'+GERMAN*30,ENGLISH])[0],'ENGLISH')

    def test_conflicting_language_evidence_requires_review(self):
        self.assertEqual(language([ENGLISH*4,GERMAN*4])[0],'NEEDS REVIEW')

    def test_only_catalog_text_does_not_establish_book_language(self):
        self.assertEqual(language([CIP,SERIES])[0],'NEEDS REVIEW')

    def test_overlapping_halftone_fragments_count_as_single_picture(self):
        # Actual contour boxes from the reported Beatles first/crowd plates.
        first=[[66,75,605,558],[65,419,135,495],[65,489,605,931]]
        crowd=[[116,226,652,774],[117,568,265,775],[556,682,621,773],[267,727,363,774]]
        self.assertEqual(len(merge_picture_boxes(first)),1)
        self.assertEqual(len(merge_picture_boxes(crowd)),1)

    def test_distinct_and_touching_pictures_remain_separate(self):
        self.assertEqual(len(merge_picture_boxes([[10,10,100,100],[101,10,200,100]])),2)
        self.assertEqual(len(merge_picture_boxes([[10,10,100,100],[100,10,200,100]])),2)

    def test_chain_overlap_and_order_independence(self):
        boxes=[[0,0,100,100],[180,0,250,100],[80,0,200,100],[500,0,600,100]]
        self.assertEqual(merge_picture_boxes(boxes),[[0,0,250,100],[500,0,600,100]])
        self.assertEqual(merge_picture_boxes(boxes[::-1]),merge_picture_boxes(boxes))

    def test_ocr_words_inside_one_photo_do_not_split_its_count(self):
        rgb=np.full((1000,800,3),255,np.uint8)
        rgb[150:800,100:700]=[40,60,80]
        # A mistaken OCR word spanning the picture makes two separate contours.
        words=[(100,450,700,470,'false OCR',0,0,0)]
        self.assertEqual(len(picture_regions(rgb,words,800,1000)),1)

    def test_ocr_reconciliation_keeps_separate_photos_separate(self):
        rgb=np.full((1000,800,3),255,np.uint8)
        rgb[180:750,70:340]=[40,60,80]
        rgb[180:750,460:730]=[80,60,40]
        words=[(70,430,340,450,'false OCR',0,0,0)]
        self.assertEqual(len(picture_regions(rgb,words,800,1000)),2)

    def test_sparse_paragraph_leftovers_are_not_pictures(self):
        raw=np.zeros((100,100),np.uint8)
        for y in range(10,90,8):
            raw[y:y+2,10:90]=255
        masked=np.zeros_like(raw)
        masked[82:84,10:90]=255
        self.assertEqual(remove_ocr_text_residue([[10,80,90,85]],[[10,10,90,90]],raw,masked),[])

    def test_dense_picture_fragments_rejoin_even_when_ocr_erases_most_pixels(self):
        raw=np.zeros((100,100),np.uint8)
        raw[10:90,10:90]=255
        boxes=[[10,10,45,35],[55,65,90,90]]
        self.assertEqual(join_ocr_split_pictures(boxes,[[10,10,90,90]],raw,100,100),[[10,10,90,90]])


if __name__ == '__main__':
    unittest.main()

