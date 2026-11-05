from io import BytesIO

from PIL import Image

HEADER = b"VKMSG1"


def hide_text(image_data, text, output_path):
    image = Image.open(BytesIO(image_data)).convert("RGB")
    message = HEADER + len(text.encode("utf-8")).to_bytes(4, "big") + text.encode("utf-8")
    bits = [(byte >> bit) & 1 for byte in message for bit in range(7, -1, -1)]
    pixels = list(image.getdata())
    if len(bits) > len(pixels) * 3:
        raise ValueError("Текст слишком длинный для этой картинки")

    values = [channel for pixel in pixels for channel in pixel]
    for index, bit in enumerate(bits):
        values[index] = (values[index] & 254) | bit
    image.putdata([tuple(values[i:i + 3]) for i in range(0, len(values), 3)])
    image.save(output_path, format="PNG")


def read_text(image_data):
    pixels = Image.open(BytesIO(image_data)).convert("RGB").getdata()
    bits = (channel & 1 for pixel in pixels for channel in pixel)

    def read_bytes(count):
        result = bytearray()
        for _ in range(count):
            value = 0
            for _ in range(8):
                value = (value << 1) | next(bits)
            result.append(value)
        return bytes(result)

    if read_bytes(len(HEADER)) != HEADER:
        raise ValueError("В PNG не найдено скрытое сообщение")
    length = int.from_bytes(read_bytes(4), "big")
    return read_bytes(length).decode("utf-8")
