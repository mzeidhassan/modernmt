import configparser
import logging
import math
import os
import time

import fairseq
import numpy as np
import torch
from fairseq.models.transformer import TransformerModel
from fairseq.sequence_generator import SequenceGenerator

from mmt import textencoder, is_fairseq_0_12
from mmt.alignment import make_alignment, clean_alignment
from mmt.tuning import Tuner, TuningOptions


class Translation(object):
    def __init__(self, text, alignment=None, score=None):
        self.text = text
        self.alignment = alignment
        self.score = score


class Suggestion(object):
    def __init__(self, source_lang, target_lang, segment, translation, score):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.segment = segment
        self.translation = translation
        self.score = score


class ModelConfig(object):
    __custom_values = {'True': True, 'False': False, 'None': None}

    @classmethod
    def load(cls, model_path):
        config = configparser.ConfigParser()
        config.read(os.path.join(model_path, 'model.conf'))
        return cls(model_path, config)

    def __init__(self, base_path, config_parser):
        self._base_path = base_path
        self._config = config_parser

    def _parse(self, value):
        if value in self.__custom_values:
            value = self.__custom_values[value]
        else:
            try:
                number = float(value)
                value = number if '.' in value else int(value)
            except ValueError:
                pass  # value is a string

        return value

    @property
    def tuning(self):
        ops = TuningOptions()

        if self._config.has_section('settings'):
            for name, value in self._config.items('settings'):
                if not hasattr(ops, name):
                    raise ValueError('Invalid option "%s"' % name)
                setattr(ops, name, self._parse(value))

        return ops

    @property
    def checkpoints(self):
        def normalize_lang(lang):
            if '-' in lang:
                a, b = lang.split('-')
                return '%s-%s' % (a.lower(), b.upper())
            else:
                return lang.lower()

        result = []

        for name, value in self._config.items('models'):
            if not os.path.isabs(value):
                value = os.path.join(self._base_path, value)

            source, target = name.split('__')
            name = '%s__%s' % (normalize_lang(source), normalize_lang(target))

            result.append((name, value))

        return result


class MMTDecoder(object):
    @classmethod
    def _create_model(cls, checkpoints, device, beam_size, use_fp16):
        model = TransformerModel.build_model(checkpoints.args, checkpoints.task)

        # Custom make_generation_fast_
        eval_fn, train_fn = model.eval, model.train
        model.eval = lambda: None

        model.make_generation_fast_(
            beamable_mm_beam_size=None if beam_size == 0 else beam_size,
            need_attn=True,  # --print-alignment
        )

        model.eval, model.train = eval_fn, train_fn

        if device is not None:
            torch.cuda.set_device(device)
            model = model.cuda(device)

        if use_fp16:
            model.half()

        return model

    @classmethod
    def _create_translator(cls, models, checkpoints, beam_size):
        args = [checkpoints.task.target_dictionary]
        kwargs = {'beam_size': beam_size, 'max_len_a': 0, 'max_len_b': 1, 'min_len': 1, 'normalize_scores': True,
                  'len_penalty': 1, 'unk_penalty': 0, 'temperature': 1}

        if is_fairseq_0_12():
            args.insert(0, models)
        else:
            kwargs.update({'stop_early': True, 'sampling': False, 'sampling_topk': -1,
                           'diverse_beam_groups': 1, 'diverse_beam_strength': .5})

        return SequenceGenerator(*args, **kwargs)

    @classmethod
    def _create_tuner(cls, checkpoints, model, tuning_ops, device):
        return Tuner(checkpoints.args, checkpoints.task, model, tuning_ops=tuning_ops, device=device)

    @classmethod
    def port_to_fairseq_0_12(cls, checkpoint):
        missing_default_params = {'decoder_layers_to_keep': None, 'encoder_layers_to_keep': None,
                                  'encoder_layerdrop': 0, 'decoder_layerdrop': 0, 'quant_noise_pq': 0}
        for missing in missing_default_params.keys():
            if missing not in checkpoint.args:
                setattr(checkpoint.args, missing, missing_default_params[missing])

    def __init__(self, checkpoints, device=None, beam_size=5, use_fp16=False, tuning_ops=None):
        torch.manual_seed(checkpoints.args.seed)

        self._checkpoints = checkpoints
        self._checkpoints.args.fp16 = use_fp16

        if is_fairseq_0_12():
            self.port_to_fairseq_0_12(self._checkpoints)

        self._device = device
        self._model = self._fix_model_probs(
            self._create_model(checkpoints, device=device, beam_size=beam_size, use_fp16=use_fp16)
        )
        self._translator = self._create_translator([self._model], checkpoints, beam_size)
        self._tuner = self._create_tuner(checkpoints, self._model, tuning_ops, device)
        self._max_positions = fairseq.utils.resolve_max_positions(
            checkpoints.task.max_positions(),
            self._model.max_positions(),
        )

        self._logger = logging.getLogger('Transformer')

        self._nn_needs_reset = True
        self._checkpoint = None

    def _fix_model_probs(self, model):
        # Handling of multilingual engines with varying vocab sizes with resistance
        # to negative logits, for which we need to override `model.get_normalized_probs`
        # and keep track of the original vocabulary sizes of each model to do masking
        _model_get_normalized_log_probs = model.get_normalized_probs

        def _get_normalized_log_probs(*args, **kwargs):
            """
            Since we alter the vocabulary and embedding matrix sizes to make multiple models
            of varying sizes compatible with the same instance of TransformerModel, we have
            to do some hacky overriding to make sure that the additional words in our extended
            output logits cannot be be predicted during BeamSearch decoding. At the same time we
            have to make sure that we don't cause the softmax to behave differently.
            We solve this by treating the extended tokens the same way <PAD> is treated by setting
            their log probability to negative infinity, and ensuring they are removed before
            softmax is applied.
            """
            if self._checkpoint is None:
                return _model_get_normalized_log_probs(*args, **kwargs)

            sub_dict = self._checkpoint.subword_dictionary

            # net_output can be both a tuple and a list, so we have to handle both
            net_output, *args_rest = args
            logits, *net_output_rest = net_output

            # truncate log probs to actual vocab size for proper softmax distribution
            fixed_logits = logits[:, :, :sub_dict.original_size]

            # stitch *args tuple back together again with fixed_logits
            fixed_args = ((fixed_logits, *net_output_rest), *args_rest)

            # call the original _get_normalized_log_probs with fixed_logits
            log_probs = _model_get_normalized_log_probs(*fixed_args, **kwargs)

            # pad the probs to extended size (applied as left/right padding to last dim)
            pad_size = len(sub_dict) - sub_dict.original_size
            padded_probs = torch.nn.functional.pad(
                log_probs, (0, pad_size), mode="constant", value=-math.inf
            )
            return padded_probs

        model.get_normalized_probs = _get_normalized_log_probs
        return model

    # - High level functions -------------------------------------------------------------------------------------------

    def test(self):
        test_batch, _, _ = self._make_decode_batch([])

        begin = time.time()
        self._translator.max_len_b = 1
        self._translator.generate([self._model], test_batch)
        test_time = time.time() - begin

        self._logger.info('test_time = %.3f' % test_time)

    def translate(self, source_lang, target_lang, batch, suggestions=None,
                  tuning_epochs=None, tuning_learning_rate=None, forced_translation=None):
        # (1) Reset model (if necessary)
        begin = time.time()
        self._reset_model(source_lang, target_lang)
        reset_time = time.time() - begin

        # (2) Tune engine if suggestions provided
        begin = time.time()
        if suggestions is not None and len(suggestions) > 0:
            self._tune(suggestions, epochs=tuning_epochs, learning_rate=tuning_learning_rate)
        tune_time = time.time() - begin

        # (3) Translate and compute word alignment
        begin = time.time()
        if forced_translation is not None:
            result = self._force_decode(target_lang, batch, forced_translation)
        else:
            result = self._decode(source_lang, target_lang, batch)

        decode_time = time.time() - begin

        self._logger.info('reset_time = %.3f, tune_time = %.3f, decode_time = %.3f'
                          % (reset_time, tune_time, decode_time))

        return result

    # - Low level functions --------------------------------------------------------------------------------------------

    def _reset_model(self, source_lang, target_lang):
        checkpoint = self._checkpoints.load(source_lang, target_lang)

        if self._nn_needs_reset or checkpoint != self._checkpoint:
            self._model.load_state_dict(checkpoint.state, strict=True)
            self._checkpoint = checkpoint
            self._nn_needs_reset = False

    def _tune(self, suggestions, epochs=None, learning_rate=None):
        # Set tuning parameters
        if epochs is None or learning_rate is None:
            _epochs, _learning_rate = self._tuner.estimate_tuning_parameters(suggestions)

            epochs = epochs if epochs is not None else _epochs
            learning_rate = learning_rate if learning_rate is not None else _learning_rate

        # Run tuning
        if learning_rate > 0. and epochs > 0:
            sub_dict = self._checkpoint.subword_dictionary

            if self._checkpoint.multilingual_target:
                src_samples = [sub_dict.language_tag(e.target_lang) + ' ' + e.segment for e in suggestions]
            else:
                src_samples = [e.segment for e in suggestions]
            tgt_samples = [e.translation for e in suggestions]

            dataset = self._tuner.dataset(src_samples, tgt_samples, sub_dict)
            self._tuner.tune(dataset, num_iterations=epochs, lr=learning_rate)
            self._nn_needs_reset = True

    def _decode(self, source_lang, target_lang, segments):
        prefix_lang = target_lang if self._checkpoint.multilingual_target else None
        batch, input_indexes, sentence_len = self._make_decode_batch(segments, prefix_lang=prefix_lang)

        # Compute translation
        self._translator.max_len_b = self._checkpoint.decode_length(source_lang, target_lang, sentence_len)
        translations = self._translator.generate([self._model], batch)

        # Decode translation
        sub_dict = self._checkpoint.subword_dictionary

        results = []
        for i, hypo in enumerate(translations):
            hypo = hypo[0]  # (top-1 best nbest)
            hypo_score = math.exp(hypo['score'])
            hypo_tokens = hypo['tokens']
            hypo_indexes = sub_dict.indexes_of(hypo_tokens)
            hypo_str = sub_dict.string(hypo_tokens)
            hypo_attention = np.asarray(hypo['attention'].data.cpu())

            # Make alignment
            if len(hypo_indexes) > 0:
                hypo_alignment = make_alignment(input_indexes[i], hypo_indexes, hypo_attention,
                                                prefix_lang=prefix_lang is not None)
                hypo_alignment = clean_alignment(hypo_alignment, segments[i], hypo_str)
            else:
                hypo_alignment = []

            results.append(Translation(hypo_str, alignment=hypo_alignment, score=hypo_score))

        return results

    def _force_decode(self, target_lang, segments, translations):
        prefix_lang = target_lang if self._checkpoint.multilingual_target else None

        batch = self._make_force_decode_batch(segments, translations, prefix_lang=prefix_lang)

        src_tokens = batch['src_tokens']
        tgt_tokens = batch['trg_tokens']
        src_indexes = batch['src_indexes']
        tgt_indexes = batch['trg_indexes']
        src_lengths = batch['src_lengths']

        if self._device is not None:
            src_tokens = src_tokens.cuda(self._device)
            src_lengths = src_lengths.cuda(self._device)
            tgt_tokens = tgt_tokens.cuda(self._device)

        self._model.eval()
        _, attn = self._model(src_tokens, src_lengths, tgt_tokens)
        if type(attn) is dict:
            attn = attn['attn']
        if type(attn) is list:
            attn = attn[0]

        results = []
        for i, hypo_attention in enumerate(attn):  # for each entry of the original batch
            hypo_attention = hypo_attention.transpose(0, 1).cpu()
            hypo_attention = hypo_attention[hypo_attention.size(0) - (len(src_indexes[i]) + 1):,
                             hypo_attention.size(1) - (len(tgt_indexes[i]) + 1):]

            # Make alignment
            hypo_alignment = make_alignment(src_indexes[i], tgt_indexes[i], hypo_attention.data.numpy(),
                                            prefix_lang=prefix_lang is not None)

            hypo_alignment = clean_alignment(hypo_alignment, segments[i], translations[i])

            results.append(Translation(translations[i], alignment=hypo_alignment))

        return results

    def _make_decode_batch(self, segments, prefix_lang=None):
        src_tokens, src_indexes, src_lengths, src_max_length = self._make_batch(segments, prefix_lang=prefix_lang)

        batch = {'net_input': {
            'src_tokens': src_tokens,
            'src_lengths': src_lengths
        }}

        return batch, src_indexes, src_max_length

    def _make_force_decode_batch(self, segments, translations, prefix_lang=None):
        src_tokens, src_indexes, src_lengths, _ = self._make_batch(segments, prefix_lang=prefix_lang)
        trg_tokens, trg_indexes, _, _ = self._make_batch(translations, reverse_last_word=True)

        return {
            'src_tokens': src_tokens,
            'trg_tokens': trg_tokens,
            'src_indexes': src_indexes,
            'trg_indexes': trg_indexes,
            'src_lengths': src_lengths
        }

    def _make_batch(self, entries, prefix_lang=None, reverse_last_word=False):
        # Add language prefix if multilingual target
        if prefix_lang is not None:
            entries = [self._checkpoint.subword_dictionary.language_tag(prefix_lang) + ' ' + text for text in entries]

        # Prepare batch
        if len(entries) > 0:
            sub_dict = self._checkpoint.subword_dictionary

            tokens = [
                sub_dict.encode_line(text, line_tokenizer=sub_dict.tokenize, add_if_not_exist=False).long()
                for text in entries
            ]
            indexes = [sub_dict.indexes_of(el) for el in tokens]
            lengths = torch.LongTensor([t.numel() for t in tokens])

            if reverse_last_word:
                tokens = [torch.cat((text[-1:], text[:-1])) for text in tokens]
        else:
            tokens = torch.LongTensor([[textencoder.EOS_ID]])
            indexes = [[]]
            lengths = torch.LongTensor([1])

        max_length = torch.max(lengths)

        # Apply padding
        if len(entries) > 1:
            tokens = [torch.nn.functional.pad(el, (max_length - el.size(0), 0), value=sub_dict.pad()) for el in tokens]

        # Reshape tokens tensor
        if len(entries) > 1:
            tokens = torch.cat(tokens)
        else:
            tokens = tokens[0]

        tokens = tokens.reshape([max(1, len(entries)), max_length])

        if self._device is not None:
            tokens = tokens.cuda(self._device)
            lengths = lengths.cuda(self._device)

        return tokens, indexes, lengths, max_length
