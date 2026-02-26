import os
import httpx
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageChops


class CardGenerator:
    def __init__(self):
        # 1. 캔버스 및 색상 설정
        self.card_size = (400, 600)
        self.bg_color = (250, 250, 250)
        self.text_color = (40, 40, 40)  # 기본 진회색
        self.border_color = (75, 75, 75)  # 회색 테두리
        self.red_color = (220, 20, 60)  # 포인트 레드 (레벨용)
        self.blue_color = (0, 100, 240)  # 포인트 블루 (전투력용)

        self.outer_padding = 12
        self.outer_border_width = 4

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
        if eok > 0: result += f"{eok}억"
        if man > 0: result += f"{man}만"
        if won > 0 or not result: result += f"{won}"
        return result

    async def create_card(self, data: dict):
        card = Image.new("RGB", self.card_size, self.bg_color)
        draw = ImageDraw.Draw(card)

        # 3. 캐릭터 원형 프레임 (기존 로직 유지)
        char_diameter = 200
        char_x = (self.card_size[0] - char_diameter) // 2
        char_y = 60
        inner_border_width = 5

        draw.ellipse(
            (char_x - inner_border_width, char_y - inner_border_width,
             char_x + char_diameter + inner_border_width, char_y + char_diameter + inner_border_width),
            fill=self.border_color
        )

        async with httpx.AsyncClient() as client:
            try:
                img_res = await client.get(data['image'])
                char_img_rgba = Image.open(BytesIO(img_res.content)).convert("RGBA")
                char_img_large = char_img_rgba.resize((500, 500), Image.Resampling.LANCZOS)
                char_square = char_img_large.crop((150, 150, 350, 350))

                scale = 2
                mask_big = Image.new('L', (char_diameter * scale, char_diameter * scale), 0)
                draw_mask = ImageDraw.Draw(mask_big)
                draw_mask.ellipse((0, 0, char_diameter * scale, char_diameter * scale), fill=255)
                mask_smooth = mask_big.resize((char_diameter, char_diameter), Image.Resampling.LANCZOS)

                char_alpha = char_square.split()[3]
                combined_mask = ImageChops.multiply(char_alpha, mask_smooth)
                char_final = char_square.copy()
                char_final.putalpha(combined_mask)
                card.paste(char_final, (char_x, char_y), char_final)
            except Exception as e:
                print(f"이미지 처리 오류: {e}")

        # 4. 폰트 로드
        try:
            title_font = ImageFont.truetype(self.font_bold, 44)
            info_font = ImageFont.truetype(self.font_light, 26)
            class_font = ImageFont.truetype(self.font_light, 20)
        except:
            title_font = ImageFont.load_default()
            info_font = ImageFont.load_default()
            class_font = ImageFont.load_default()

        # 5. 텍스트 배치 시작 (중앙 정렬 좌표 계산 최적화)

        # 5-1. 닉네임 (Bold)
        name_text = data['name']
        name_bbox = draw.textbbox((0, 0), name_text, font=title_font)
        name_x = (self.card_size[0] - (name_bbox[2] - name_bbox[0])) // 2
        draw.text((name_x, 300), name_text, fill=self.text_color, font=title_font)

        # 5-2. 직업 (닉네임 바로 아래 배치)
        class_text = data['class']
        class_bbox = draw.textbbox((0, 0), class_text, font=info_font)
        class_x = (self.card_size[0] - (class_bbox[2] - class_bbox[0])) // 2
        draw.text((class_x, 355), class_text, fill=self.text_color, font=class_font)

        # 5-3. 월드 + 레벨 (한 줄 배치 및 수치 강조)
        # 텍스트 예: "스카니아  Lv. 285" (285만 빨간색)
        world_label = f"{data['world']}     "
        level_value = f"Lv.{data['level']}"

        # 전체 너비 계산 (중앙 정렬용)
        w_label = draw.textbbox((0, 0), world_label, font=info_font)[2]
        w_level = draw.textbbox((0, 0), level_value, font=info_font)[2]
        total_w_line1 = w_label + w_level
        start_x_line1 = (self.card_size[0] - total_w_line1) // 2

        y_line1 = 420
        draw.text((start_x_line1, y_line1), world_label, fill=self.text_color, font=info_font)
        draw.text((start_x_line1 + w_label, y_line1), level_value, fill=self.red_color, font=info_font)

        # 5-4. 전투력 (파란색 포인트)
        combat_power_text = self.format_korean_unit(int(data.get('combat_power', 0)))
        cp_bbox = draw.textbbox((0, 0), combat_power_text, font=info_font)
        cp_x = (self.card_size[0] - (cp_bbox[2] - cp_bbox[0])) // 2
        draw.text((cp_x, 500), combat_power_text, fill=self.blue_color, font=info_font)

        # 6. 전체 외곽 테두리 그리기
        pad = self.outer_padding
        draw.rectangle(
            (pad, pad, self.card_size[0] - pad - 1, self.card_size[1] - pad - 1),
            outline=self.border_color,
            width=self.outer_border_width
        )

        # 7. 결과 반환
        img_byte_arr = BytesIO()
        card.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        return img_byte_arr