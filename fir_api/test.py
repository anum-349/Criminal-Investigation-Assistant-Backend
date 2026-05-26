from paddleocr import PaddleOCR
import numpy as np
from PIL import Image

ocr = PaddleOCR(
    use_angle_cls=True,
    lang='arabic',
    drop_score=0.2,
    det_db_box_thresh=0.2,
    det_db_unclip_ratio=2.5,
    rec_batch_num=1,
    show_log=False
)

img = np.array(Image.open('urdu_fir.png').convert('RGB'))

result = ocr.ocr(img)

full_text = []

for line in result[0]:
    text = line[1][0]
    full_text.append(text)

print("\n".join(full_text))