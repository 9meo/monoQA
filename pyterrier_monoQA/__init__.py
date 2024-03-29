import sys
import math
import warnings
import itertools
import pyterrier as pt
import pandas as pd
from collections import defaultdict
from pyterrier.model import add_ranks
import torch
from torch.nn import functional as F
from transformers import T5Config, T5Tokenizer, T5ForConditionalGeneration
from pyterrier.transformer import TransformerBase
from typing import List
import re
from transformers import set_seed

class MonoQA(TransformerBase):
    def __init__(self, 
                 tok_model='t5-base',
                 model='castorini/monot5-base-msmarco',
                 batch_size=4,
                 text_field='text',
                 verbose=True):
        self.verbose = verbose
        self.batch_size = batch_size
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.tokenizer = T5Tokenizer.from_pretrained(tok_model)
        self.model_name = model
        self.model = T5ForConditionalGeneration.from_pretrained(model)
        self.model.to(self.device)
        self.model.eval()
        self.text_field = text_field
        self.REL = self.tokenizer.encode('true')[0]
        self.NREL = self.tokenizer.encode('false')[0]

    def __str__(self):
        return f"MonoQA({self.model_name})"
    
    def run_model(self, input_ids):
        res = self.model.generate(input_ids)
        return self.tokenizer.batch_decode(res, skip_special_tokens=True)
    def qr(self, input_ids):
        set_seed(42)
        beam_outputs = self.model.generate(
            input_ids=input_ids,# attention_mask=attention_masks,
            do_sample=True,
            max_length=128,
#             top_k=120,
            top_k=60,
            top_p=0.98,
            early_stopping=True,
            num_beams=1,
            num_return_sequences=1
        )
        final_outputs =[]
        for beam_output in beam_outputs:
            sent = self.tokenizer.decode(beam_output, skip_special_tokens=True,clean_up_tokenization_spaces=True)
            final_outputs.append(sent)
        return final_outputs
    def transform(self, run):
        scores = []
        answers = []
        queries, texts = run['query'], run[self.text_field]
        max_input_length = 512
        it = range(0, len(queries), self.batch_size)
#         prompts = self.tokenizer.batch_encode_plus([f'Relevant:' for _ in range(self.batch_size)], return_tensors='pt', padding='longest')
#         max_vlen = self.model.config.n_positions - prompts['input_ids'].shape[1]
        if self.verbose:
            it = pt.tqdm(it, desc='monoT5', unit='batches')
        for start_idx in it:
            rng = slice(start_idx, start_idx+self.batch_size) # same as start_idx:start_idx+self.batch_size
#             enc = self.tokenizer.batch_encode_plus([f'Question: {q} Passage: {d}' for q, d in zip(queries[rng], texts[rng])], return_tensors='pt', padding='longest')
            enc = self.tokenizer.batch_encode_plus([f'Question Answering: {q} <extra_id_0> {d}' for q, d in zip(queries[rng], texts[rng])], return_tensors='pt', padding=True, max_length=max_input_length)
#             for key, enc_value in list(enc.items()):
#                 enc_value = enc_value[:, :-1] # chop off end of sequence token-- this will be added with the prompt
#                 enc_value = enc_value[:, :max_vlen] # truncate any tokens that will not fit once the prompt is added
#                 enc[key] = torch.cat([enc_value, prompts[key][:enc_value.shape[0]]], dim=1) # add in the prompt to the end
            input_ids  = enc['input_ids'].to(self.device)
            enc['decoder_input_ids'] = torch.full(
                (len(queries[rng]), 1),
                self.model.config.decoder_start_token_id,
                dtype=torch.long
            )
            enc = {k: v.to(self.device) for k, v in enc.items()}
            with torch.no_grad():
                set_seed(42)
                result = self.model(**enc).logits
            result = result[:, 0, (self.REL, self.NREL)]
            scores += F.log_softmax(result, dim=1)[:, 0].cpu().detach().tolist()
            answers.extend(self.run_model(input_ids))
#             answers.extend(self.qr(input_ids))
        run = run.drop(columns=['score', 'rank'], errors='ignore').assign(score=scores)
        run = run.assign(answer=answers)
        run = add_ranks(run)
        return run



