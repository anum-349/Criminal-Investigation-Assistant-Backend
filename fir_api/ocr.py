import easyocr

reader = easyocr.Reader(['ur','en'])

result = reader.readtext('processed.jpg', detail=0)

print(result)