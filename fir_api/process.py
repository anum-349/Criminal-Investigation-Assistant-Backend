import cv2

img = cv2.imread("urdu_fir.png")

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Increase contrast
gray = cv2.equalizeHist(gray)

# Threshold
thresh = cv2.threshold(
    gray,
    0,
    255,
    cv2.THRESH_BINARY + cv2.THRESH_OTSU
)[1]

# Denoise
denoise = cv2.fastNlMeansDenoising(thresh)

cv2.imwrite("processed.jpg", denoise)