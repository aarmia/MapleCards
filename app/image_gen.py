from PIL import Image, ImageDraw, ImageFont
import httpx
from io import BytesIO


class CardGenerator:
    def __init__(self):
        # 1. 캔버스 크기 재조정 (불필요한 공백 제거를 위해 크기 축소)
        self.card_size = (400, 650)
        self.bg_color = (250, 250, 250)  # 단색 배경
        self.text_color = (40, 40, 40)  # 진한 회색 텍스트

    async def create_card(self, data: dict):
        card = Image.new("RGB", self.card_size, self.bg_color)
        draw = ImageDraw.Draw(card)

        # 2. 캐릭터 이미지 가져오기
        async with httpx.AsyncClient() as client:
            img_res = await client.get(data['image'])
            char_img = Image.open(BytesIO(img_res.content)).convert("RGBA")

        # === 핵심 로직: 고화질 리사이즈 후 중앙 크롭 ===
        # step 1: 먼저 고화질을 위해 이미지를 크게(500x500) 키웁니다.
        # (LANCZOS 필터를 사용하면 깨짐이 덜합니다.)
        char_img_large = char_img.resize((500, 500), Image.Resampling.LANCZOS)

        # step 2: 500x500 이미지의 정중앙 250x250 영역을 계산합니다.
        # 중앙 좌표는 (250, 250)이므로, 여기서 좌우상하로 125씩 떨어진 영역을 잡습니다.
        # crop 영역: (left, top, right, bottom)
        crop_box = (125, 125, 375, 375)
        char_cropped = char_img_large.crop(crop_box)

        # 3. 크롭된 이미지(250x250) 중앙 배치
        char_x = (self.card_size[0] - char_cropped.width) // 2  # (400-250)//2 = 75
        char_y = 60  # 상단 여백
        card.paste(char_cropped, (char_x, char_y), char_cropped)

        # 4. 폰트 설정 (캔버스에 맞춰 크기 약간 조절)
        try:
            font_path = "C:/Windows/Fonts/malgunbd.ttf"
            title_font = ImageFont.truetype(font_path, 32)  # 닉네임
            info_font = ImageFont.truetype(font_path, 20)  # 상세정보
        except:
            title_font = ImageFont.load_default()
            info_font = ImageFont.load_default()

        # 5. 이름 텍스트 (중앙 정렬)
        name_text = data['name']
        bbox = draw.textbbox((0, 0), name_text, font=title_font)
        text_w = bbox[2] - bbox[0]
        # 이미지 바로 아래에 위치하도록 y좌표 조정
        draw.text(((self.card_size[0] - text_w) // 2, 330), name_text, fill=self.text_color, font=title_font)

        # 6. 상세 정보 라인 (요청하신 대로 레이블 제거 및 중앙 정렬)
        info_y = 385
        details = [
            f"{data['world']}",  # "World: " 제거
            f"{data['class']}",  # "Job: " 제거
            f"Lv. {data['level']}",  # 레벨은 숫자만 있으면 허전해서 Lv. 붙임 (제거 원하시면 말씀해주세요)
            f"{int(data['combat_power']):,}"  # "Combat Power: " 제거 및 콤마 적용
        ]

        # 모든 정보를 중앙 정렬로 배치
        for detail in details:
            bbox = draw.textbbox((0, 0), detail, font=info_font)
            text_w = bbox[2] - bbox[0]
            draw.text(((self.card_size[0] - text_w) // 2, info_y), detail, fill=self.text_color, font=info_font)
            info_y += 35  # 줄 간격

        # 7. 결과 반환
        img_byte_arr = BytesIO()
        card.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        return img_byte_arr