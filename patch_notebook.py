import copy
from pathlib import Path

import json


NOTEBOOK = Path(r"C:\Users\ASUS\Downloads\DichThuatHoaNgu (1).ipynb")
BACKUP = NOTEBOOK.with_name("DichThuatHoaNgu (1).before_split_fix.ipynb")


def replace_cell(nb, index, source):
    nb['cells'][index]['source'] = source.strip() + "\n"
    nb['cells'][index]['outputs'] = []
    nb['cells'][index]['execution_count'] = None


nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
if len(nb['cells']) < 12:
    raise RuntimeError("Notebook has an unexpected number of cells.")

if not BACKUP.exists():
    BACKUP.write_text(json.dumps(copy.deepcopy(nb), ensure_ascii=False, indent=1), encoding="utf-8")

replace_cell(nb, 2, r'''
from pathlib import Path
import random

# Làm sạch target và tách train/validation TRƯỚC khi train tokenizer.
# Không bỏ dòng rỗng độc lập ở hai ngôn ngữ vì điều đó có thể làm lệch cặp câu.
SEED = 42
VAL_RATIO = 0.10

RAW_ZH_PATH = Path('train.zh')
RAW_VI_PATH = Path('train.vi')

zh_lines = RAW_ZH_PATH.read_text(encoding='utf-8').splitlines()
vi_lines_raw = RAW_VI_PATH.read_text(encoding='utf-8').splitlines()

if len(zh_lines) != len(vi_lines_raw):
    raise ValueError(f'Số dòng không khớp: zh={len(zh_lines)}, vi={len(vi_lines_raw)}')

pairs = []
for line_no, (zh, vi) in enumerate(zip(zh_lines, vi_lines_raw), start=1):
    zh = zh.strip()
    vi = ' '.join(vi.replace('_', ' ').split())
    if not zh or not vi:
        raise ValueError(f'Dòng {line_no} rỗng ở train.zh hoặc train.vi; hãy xử lý theo cặp, không lọc riêng từng file.')
    pairs.append((zh, vi))

rng = random.Random(SEED)
indices = list(range(len(pairs)))
rng.shuffle(indices)
val_size = max(1, int(len(pairs) * VAL_RATIO))
val_indices = set(indices[:val_size])

splits = {
    'split_train.zh': [], 'split_train.vi': [],
    'split_val.zh': [], 'split_val.vi': [],
}
for index, (zh, vi) in enumerate(pairs):
    prefix = 'split_val' if index in val_indices else 'split_train'
    splits[f'{prefix}.zh'].append(zh)
    splits[f'{prefix}.vi'].append(vi)

for filename, lines in splits.items():
    Path(filename).write_text('\n'.join(lines) + '\n', encoding='utf-8')

print(f'Đã làm sạch và chia dữ liệu với seed={SEED}: '
      f"train={len(splits['split_train.zh'])}, val={len(splits['split_val.zh'])}")
''')

replace_cell(nb, 3, r'''
import sentencepiece as spm

# Tokenizer chỉ được học từ split_train để validation là dữ liệu chưa thấy.
TRAIN_ZH_PATH = 'split_train.zh'
TRAIN_VI_PATH = 'split_train.vi'
ZH_MODEL_PREFIX = 'spm_zh'
VI_MODEL_PREFIX = 'spm_vi'
VOCAB_SIZE_ZH = 4000
VOCAB_SIZE_VI = 8000

spm.SentencePieceTrainer.train(
    input=TRAIN_ZH_PATH, model_prefix=ZH_MODEL_PREFIX,
    vocab_size=VOCAB_SIZE_ZH, character_coverage=0.9995, model_type='bpe',
    pad_id=0, unk_id=1, bos_id=2, eos_id=3,
    pad_piece='<pad>', unk_piece='<unk>', bos_piece='<bos>', eos_piece='<eos>'
)

spm.SentencePieceTrainer.train(
    input=TRAIN_VI_PATH, model_prefix=VI_MODEL_PREFIX,
    vocab_size=VOCAB_SIZE_VI, character_coverage=1.0, model_type='bpe',
    pad_id=0, unk_id=1, bos_id=2, eos_id=3,
    pad_piece='<pad>', unk_piece='<unk>', bos_piece='<bos>', eos_piece='<eos>'
)

sp_zh = spm.SentencePieceProcessor(model_file=f'{ZH_MODEL_PREFIX}.model')
sp_vi = spm.SentencePieceProcessor(model_file=f'{VI_MODEL_PREFIX}.model')
print('Tokenizer đã train xong:', sp_zh.get_piece_size(), sp_vi.get_piece_size())
''')

replace_cell(nb, 4, r'''
import torch
from torch.utils.data import Dataset, DataLoader
from torch.nn.utils.rnn import pad_sequence
import sentencepiece as spm

class TranslationDataset(Dataset):
    def __init__(self, zh_file, vi_file, sp_zh_path='spm_zh.model', sp_vi_path='spm_vi.model'):
        self.sp_zh = spm.SentencePieceProcessor(model_file=sp_zh_path)
        self.sp_vi = spm.SentencePieceProcessor(model_file=sp_vi_path)

        with open(zh_file, 'r', encoding='utf-8') as f:
            zh_lines = [line.rstrip('\n') for line in f]
        with open(vi_file, 'r', encoding='utf-8') as f:
            vi_lines = [line.rstrip('\n') for line in f]

        if len(zh_lines) != len(vi_lines):
            raise ValueError(f'Số dòng ZH/VI không khớp: {len(zh_lines)} / {len(vi_lines)}')

        self.zh_lines, self.vi_lines = [], []
        for line_no, (zh, vi) in enumerate(zip(zh_lines, vi_lines), start=1):
            if not zh.strip() or not vi.strip():
                raise ValueError(f'Dòng {line_no} rỗng; không được lọc riêng từng ngôn ngữ.')
            self.zh_lines.append(zh.strip())
            self.vi_lines.append(vi.strip())

    def __len__(self):
        return len(self.zh_lines)

    def __getitem__(self, idx):
        zh_ids = [self.sp_zh.bos_id()] + self.sp_zh.encode_as_ids(self.zh_lines[idx]) + [self.sp_zh.eos_id()]
        vi_ids = [self.sp_vi.bos_id()] + self.sp_vi.encode_as_ids(self.vi_lines[idx]) + [self.sp_vi.eos_id()]
        return torch.tensor(zh_ids, dtype=torch.long), torch.tensor(vi_ids, dtype=torch.long)


def pad_collate_fn(batch, pad_idx=0):
    zh_list, vi_list = zip(*batch)
    zh_padded = pad_sequence(zh_list, batch_first=True, padding_value=pad_idx)
    vi_padded = pad_sequence(vi_list, batch_first=True, padding_value=pad_idx)
    return zh_padded, vi_padded
''')

source = ''.join(nb['cells'][8]['source'])
source = source.replace('from torch.utils.data import Dataset, DataLoader, random_split', 'from torch.utils.data import Dataset, DataLoader')
source = source.replace("    full_dataset = TranslationDataset('train.zh', 'train_clean.vi')\n\n    # TÁCH DỮ LIỆU: 90% Train, 10% Validation để đánh giá BLEU thực tế\n    val_size = int(len(full_dataset) * 0.1)\n    train_size = len(full_dataset) - val_size\n    train_dataset, val_dataset = random_split(full_dataset, [train_size, val_size])", "    # Các split và tokenizer đã được tạo ở cell làm sạch dữ liệu.\n    train_dataset = TranslationDataset('split_train.zh', 'split_train.vi')\n    val_dataset = TranslationDataset('split_val.zh', 'split_val.vi')\n    train_size = len(train_dataset)\n    val_size = len(val_dataset)")
source = source.replace('model = Seq2SeqVanilla(VOCAB_SIZE_ZH, VOCAB_SIZE_VI, EMBED_DIM, HIDDEN_DIM, device).to(device)', "model = Seq2SeqVanilla(\n        sp_zh.get_piece_size(), sp_vi.get_piece_size(), EMBED_DIM, HIDDEN_DIM, device\n    ).to(device)")
source = source.replace('best_bleu = 0.0', 'best_bleu = float(\'-inf\')')
source = source.replace("model.load_state_dict(torch.load('best_model.pt'))", "model.load_state_dict(torch.load('best_model.pt', map_location=device, weights_only=True))")
source = source.replace(
    "                prediction = model.fc_out(output_input)\n                top1 = prediction.argmax(1)",
    "                prediction = model.fc_out(output_input)\n                # BOS/PAD không hợp lệ ở giữa câu dịch.\n                prediction[:, sp_vi.pad_id()] = -float('inf')\n                prediction[:, sp_vi.bos_id()] = -float('inf')\n                top1 = prediction.argmax(1)"
)
source = source.replace(
    "                ref_text = clean_vietnamese_text(sp_vi.decode(trg[i].tolist()))",
    "                ref_ids = trg[i].tolist()[1:]  # bỏ BOS\n                if sp_vi.eos_id() in ref_ids:\n                    ref_ids = ref_ids[:ref_ids.index(sp_vi.eos_id())]\n                ref_text = clean_vietnamese_text(sp_vi.decode(ref_ids))"
)
source = source.replace(
    "                prediction = model.fc_out(output_input) # (1, trg_vocab_size)\n                log_probs = F.log_softmax(prediction, dim=-1).squeeze(0)",
    "                prediction = model.fc_out(output_input) # (1, trg_vocab_size)\n                prediction[:, sp_vi.pad_id()] = -float('inf')\n                prediction[:, sp_vi.bos_id()] = -float('inf')\n                log_probs = F.log_softmax(prediction, dim=-1).squeeze(0)"
)
source = source.replace(
    "        finished_beams = []\n\n        for _ in range(max_len):",
    "        finished_beams = []\n\n        def normalized_score(beam, alpha=0.6):\n            # Length penalty để beam search không ưu tiên vô lý các câu quá ngắn.\n            length = max(1, len(beam['sequence']) - 1)\n            return beam['score'] / (length ** alpha)\n\n        for _ in range(max_len):"
)
source = source.replace(
    "            beams = sorted(new_beams, key=lambda b: b['score'], reverse=True)[:beam_size]",
    "            beams = sorted(new_beams, key=normalized_score, reverse=True)[:beam_size]"
)
source = source.replace(
    "        best_beam = max(finished_beams, key=lambda b: b['score'])",
    "        best_beam = max(finished_beams, key=normalized_score)"
)
source = source.replace(
    "    # 2. Dá»‹ch tá»± Ä‘á»™ng 2 file test Ä‘á»ƒ ná»™p bÃ i\n    translate_file('public_test.zh', 'predict_public.vi', model, sp_zh, sp_vi, device, beam_size=BEAM_SIZE)\n    translate_file('private_test.zh', 'predict_private.vi', model, sp_zh, sp_vi, device, beam_size=BEAM_SIZE)",
    "    # private_test.zh là file dùng để tạo submission ở cell cuối."
)
source = source.replace("    translate_file('public_test.zh', 'predict_public.vi', model, sp_zh, sp_vi, device, beam_size=BEAM_SIZE)\n", "")
source = source.replace("    translate_file('private_test.zh', 'predict_private.vi', model, sp_zh, sp_vi, device, beam_size=BEAM_SIZE)\n", "")
nb['cells'][8]['source'] = source
nb['cells'][8]['outputs'] = []
nb['cells'][8]['execution_count'] = None

source = ''.join(nb['cells'][11]['source'])
source = source.replace("model.load_state_dict(torch.load('best_model.pt'))", "model.load_state_dict(torch.load('best_model.pt', map_location=device, weights_only=True))")
source = source.replace("export_submission_csv('public_test.zh', 'public_submission.csv', model, sp_zh, sp_vi, device)", "export_submission_csv('private_test.zh', 'private_submission.csv', model, sp_zh, sp_vi, device, beam_size=1)")
source = source.replace('public_submission.csv', 'private_submission.csv')
nb['cells'][11]['source'] = source
nb['cells'][11]['outputs'] = []
nb['cells'][11]['execution_count'] = None

source = ''.join(nb['cells'][9]['source'])
source = source.replace('translate_sentence(test_zh, model, sp_zh, sp_vi, device)', 'translate_sentence(test2_zh, model, sp_zh, sp_vi, device)')
nb['cells'][9]['source'] = source
nb['cells'][9]['outputs'] = []
nb['cells'][9]['execution_count'] = None

replace_cell(nb, 10, r'''
# Kiểm tra việc làm sạch dấu gạch dưới ngay trước khi chia train/validation.
print('--- KIỂM TRA LÀM SẠCH train.vi ---')
for i, (zh, vi_raw) in enumerate(zip(zh_lines[:5], vi_lines_raw[:5]), start=1):
    vi_clean = ' '.join(vi_raw.replace('_', ' ').split())
    print(f'Mẫu {i}')
    print('  ZH       :', zh)
    print('  VI gốc   :', vi_raw)
    print('  VI sạch  :', vi_clean)

print('\nTokenizer và model sử dụng split_train.vi / split_val.vi đã được làm sạch.')
''')

NOTEBOOK.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f'Patched: {NOTEBOOK}')
print(f'Backup: {BACKUP}')
