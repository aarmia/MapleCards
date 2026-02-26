from PIL import Image, ImageDraw, ImageFont
import httpx
from io import BytesIO


class CardGenerator:
    def __init__(self):
        self.card_size = (400, 650)
        self.bg_color = (250, 250, 250)
        self.text_color = (40, 40, 40)

    def format_korean_unit(self, number: int) -> str:
        """전투력을 억/만 단위로 변환합니다 (예: 1억7253만3345)"""
        if number == 0: return "0"

        # 억 단위 추출
        eok = number // 100_000_000
        # 만 단위 추출
        remainder = number % 100_000_000
        man = remainder // 10_000
        # 나머지 (일 단위)
        won = remainder % 10_000

        result = ""
        if eok > 0:
            result += f"{eok}억"
        if man > 0:
            result += f"{man}만"
        if won > 0 or not result:  # 0이거나 일의 자리가 있는 경우
            result += f"{won}"

        return result

    async def create_card(self, data: dict):
        card = Image.new("RGB", self.card_size, self.bg_color)
        draw = ImageDraw.Draw(card)

        # 캐릭터 이미지 가져오기 및 크롭 로직 (기존과 동일)
        async with httpx.AsyncClient() as client:
            img_res = await client.get(data['image'])
            char_img = Image.open(BytesIO(img_res.content)).convert("RGBA")

        char_img_large = char_img.resize((500, 500), Image.Resampling.LANCZOS)
        crop_box = (125, 125, 375, 375)
        char_cropped = char_img_large.crop(crop_box)

        char_x = (self.card_size[0] - char_cropped.width) // 2
        card.paste(char_cropped, (char_x, 60), char_cropped)

        # 폰트 설정
        try:
            font_path = "C:/Windows/Fonts/malgunbd.ttf"
            title_font = ImageFont.truetype(font_path, 32)
            info_font = ImageFont.truetype(font_path, 20)
        except:
            title_font = ImageFont.load_default()
            info_font = ImageFont.load_default()

        # 이름 출력
        name_text = data['name']
        bbox = draw.textbbox((0, 0), name_text, font=title_font)
        draw.text(((self.card_size[0] - (bbox[2] - bbox[0])) // 2, 330), name_text, fill=self.text_color,
                  font=title_font)

        # === 상세 정보 라인 (전투력 포맷 변경 적용) ===
        info_y = 385
        combat_power_val = int(data.get('combat_power', 0))
        korean_combat_power = self.format_korean_unit(combat_power_val)  # 단위 변환 호출

        details = [
            f"{data['world']}",
            f"{data['class']}",
            f"Lv. {data['level']}",
            f"{korean_combat_power}"  # 변환된 텍스트 사용
        ]

        for detail in details:
            bbox = draw.textbbox((0, 0), detail, font=info_font)
            text_w = bbox[2] - bbox[0]
            draw.text(((self.card_size[0] - text_w) // 2, info_y), detail, fill=self.text_color, font=info_font)
            info_y += 35

        img_byte_arr = BytesIO()
        card.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        return img_byte_arr