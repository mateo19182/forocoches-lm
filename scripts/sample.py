"""Generate from fixed prompts to eyeball a checkpoint (and compare against the base model).

    .venv-train/bin/python scripts/sample.py --model runs/v0/final --out samples/v0_gen.md
    .venv-train/bin/python scripts/sample.py --model unsloth/gemma-3-1b-pt --out samples/base_gen.md
"""

import argparse

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

# (name, prompt, stop after one post?)
PROMPTS = [
    ("historia alta",
     "tipo: historia | calidad: alta\n\nTÍTULO: Lo que me ha pasado hoy en el Mercadona +tocho\n\n#1 OP:", False),
    ("historia sin título",
     "tipo: historia | calidad: alta | páginas: 6-10\n\nTÍTULO:", False),
    ("hipotetico",
     "tipo: hipotetico\n\nTÍTULO: Os dan 1 millón de euros pero", False),
    ("humor",
     "tipo: humor | etiquetas: +hd\n\nTÍTULO:", False),
    ("sin cabecera",
     "TÍTULO:", False),
    ("respuesta a OP del usuario",
     "TÍTULO: Mi vecino tiene un loro que me insulta cada mañana +serio\n\n"
     "#1 OP: Pues eso shurs, el loro del vecino de enfrente me suelta 'gilipollas' cada vez que salgo al balcón. "
     "He hablado con el vecino y dice que él no le ha enseñado nada. ¿Qué hago?\n\n#2 forero_a:", True),
    ("respuesta citando",
     "TÍTULO: ¿Merece la pena comprarse un coche eléctrico en 2020?\n\n"
     "#1 OP: Estoy pensando en cambiar de coche y no sé si dar el salto al eléctrico.\n\n"
     "#2 forero_a: Ni de coña, cuando la batería te deje tirado en mitad de la Mancha ya me contarás\n\n"
     "#3 OP: [cita=forero_a]Ni de coña, cuando la batería te deje tirado en mitad de la Mancha ya me contarás[/cita]\n"
     "Hombre, tampoco hago tantos viajes largos\n\n#4 forero_a:", True),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=2, help="samples per prompt")
    ap.add_argument("--max-new", type=int, default=400)
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16).to("cuda").eval()

    torch.manual_seed(0)
    with open(args.out, "w") as f:
        f.write(f"# Samples from `{args.model}`\n\ntemperature 0.8, top_p 0.95, repetition_penalty 1.1\n\n")
        for name, prompt, one_post in PROMPTS:
            f.write(f"## {name}\n\n")
            ids = tok(prompt, return_tensors="pt").to("cuda")
            for i in range(args.n):
                out = model.generate(
                    **ids, do_sample=True, temperature=0.8, top_p=0.95, repetition_penalty=1.1,
                    max_new_tokens=150 if one_post else args.max_new,
                    stop_strings=["\n\n#"] if one_post else None, tokenizer=tok,
                )
                text = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
                if one_post:
                    text = text.split("\n\n#")[0]
                f.write(f"**{i + 1}.**\n\n```\n{prompt}{text}\n```\n\n")
            print(f"done: {name}")


if __name__ == "__main__":
    main()
