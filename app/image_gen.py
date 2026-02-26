import os
import httpx
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageChops


class CardGenerator:
    def __init__(self):
        # 1. 캔버스 설정 (원이 작아졌으므로 세로 길이를 600으로 최적화)
        self.card_size = (400, 600)
        self.bg_color = (250, 250, 250)
        self.text_color = (40, 40, 40)
        self.border_color = (255, 255, 255)

        # 2. 폰트 경로 설정
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.font_bold = os.path.join(base_path, "static", "fonts", "Maplestory Bold.ttf")
        self.font_light = os.path.join(base_path, "static", "fonts", "Maplestory Light.ttf")

    def format_korean_unit(self, number: int) -> str:
        """전투력을 억/만 단위로 변환 (예: 1억7253만3345)"""
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
        # 도화지 생성
        card = Image.new("RGB", self.card_size, self.bg_color)
        draw = ImageDraw.Draw(card)

        # 3. 캐릭터 이미지 및 원형 크롭 설정 (반지름 100px)
        char_diameter = 200
        char_x = (self.card_size[0] - char_diameter) // 2  # 가로 중앙 배치
        char_y = 40  # 상단 여백
        border_width = 5  # 원 크기에 맞춘 테두리 두께

        # 테두리 원 그리기 (캐릭터가 놓일 위치보다 약간 크게)
        draw.ellipse(
            (char_x - border_width, char_y - border_width,
             char_x + char_diameter + border_width, char_y + char_diameter + border_width),
            fill=self.border_color
        )

        async with httpx.AsyncClient() as client:
            try:
                img_res = await client.get(data['image'])
                char_img_rgba = Image.open(BytesIO(img_res.content)).convert("RGBA")

                # step 1: 고화질을 위해 500x500으로 먼저 리사이즈
                char_img_large = char_img_rgba.resize((500, 500), Image.Resampling.LANCZOS)

                # step 2: 중앙 200x200 크롭 (500의 정중앙인 250 기준 좌우상하 100씩)
                # crop box: (left, top, right, bottom)
                char_square = char_img_large.crop((150, 150, 350, 350))

                # step 3: 안티에일리어싱 원형 마스크 생성 (지름 200px)
                scale = 2
                mask_big = Image.new('L', (char_diameter * scale, char_diameter * scale), 0)
                draw_mask = ImageDraw.Draw(mask_big)
                draw_mask.ellipse((0, 0, char_diameter * scale, char_diameter * scale), fill=255)
                mask_smooth = mask_big.resize((char_diameter, char_diameter), Image.Resampling.LANCZOS)

                # step 4: 마스크 적용 (캐릭터 알파 채널과 결합)
                char_alpha = char_square.split()[3]
                combined_mask = ImageChops.multiply(char_alpha, mask_smooth)

                char_final = char_square.copy()
                char_final.putalpha(combined_mask)

                # 카드에 캐릭터 합성
                card.paste(char_final, (char_x, char_y), char_final)
            except Exception as e:
                print(f"이미지 처리 오류: {e}")

        # 4. 폰트 로드
        try:
            title_font = ImageFont.truetype(self.font_bold, 36)  # 닉네임
            info_font = ImageFont.truetype(self.font_light, 22)  # 상세정보
        except:
            title_font = ImageFont.load_default()
            info_font = ImageFont.load_default()

        # 5. 닉네임 출력 (원형 이미지 아래 중앙 정렬)
        name_text = data['name']
        name_bbox = draw.textbbox((0, 0), name_text, font=title_font)
        name_x = (self.card_size[0] - (name_bbox[2] - name_bbox[0])) // 2
        draw.text((name_x, 290), name_text, fill=self.text_color, font=title_font)

        # 6. 상세 정보 출력 (가운데 정렬)
        info_y = 360
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

        # 7. 결과 반환
        img_byte_arr = BytesIO()
        card.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        return img_byte_arr