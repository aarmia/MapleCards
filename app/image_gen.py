import os
import httpx
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


class CardGenerator:
    def __init__(self):
        # 1. 캔버스 및 색상 설정
        self.card_size = (400, 650)
        self.bg_color = (250, 250, 250)
        self.text_color = (40, 40, 40)

        # 2. 폰트 경로 설정
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.font_bold = os.path.join(base_path, "static", "fonts", "Maplestory Bold.ttf")
        self.font_light = os.path.join(base_path, "static", "fonts", "Maplestory Light.ttf")

    def format_korean_unit(self, number: int) -> str:
        """전투력을 억/만 단위로 변환"""
        if number == 0: return "0"

        eok = number // 100_000_000
        remainder = number % 100_000_000
        man = remainder // 10_000
        won = remainder % 10_000

        result = ""
        if eok > 0:
            result += f"{eok}억"
        if man > 0:
            result += f"{man}만"
        if won > 0 or not result:
            result += f"{won}"

        return result

    async def create_card(self, data: dict):
        card = Image.new("RGB", self.card_size, self.bg_color)
        draw = ImageDraw.Draw(card)

        # 3. 캐릭터 이미지 처리 (고화질 크롭)
        async with httpx.AsyncClient() as client:
            try:
                img_res = await client.get(data['image'])
                char_img = Image.open(BytesIO(img_res.content)).convert("RGBA")

                char_img_large = char_img.resize((500, 500), Image.Resampling.LANCZOS)
                crop_box = (125, 125, 375, 375)
                char_cropped = char_img_large.crop(crop_box)

                card.paste(char_cropped, (75, 60), char_cropped)
            except Exception as e:
                print(f"이미지 처리 오류: {e}")

        # 4. 폰트 로드 (Bold와 Light 구분)
        try:
            title_font = ImageFont.truetype(self.font_bold, 36)  # 닉네임: Bold
            info_font = ImageFont.truetype(self.font_light, 22)  # 상세정보: Light
        except:
            print("폰트 로드 실패! 기본 폰트를 사용합니다.")
            title_font = ImageFont.load_default()
            info_font = ImageFont.load_default()

        # 5. 닉네임 출력 (Bold 적용)
        name_text = data['name']
        name_bbox = draw.textbbox((0, 0), name_text, font=title_font)
        name_x = (self.card_size[0] - (name_bbox[2] - name_bbox[0])) // 2
        draw.text((name_x, 330), name_text, fill=self.text_color, font=title_font)

        # 6. 상세 정보 출력 (Light 적용 및 중앙 정렬)
        info_y = 390
        combat_power_val = int(data.get('combat_power', 0))

        details = [
            data['world'],
            data['class'],
            f"Lv. {data['level']}",
            self.format_korean_unit(combat_power_val)
        ]

        for detail in details:
            detail_bbox = draw.textbbox((0, 0), detail, font=info_font)
            detail_x = (self.card_size[0] - (detail_bbox[2] - detail_bbox[0])) // 2
            draw.text((detail_x, info_y), detail, fill=self.text_color, font=info_font)
            info_y += 45

        # 7. 이미지 결과 반환
        img_byte_arr = BytesIO()
        card.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        return img_byte_arr