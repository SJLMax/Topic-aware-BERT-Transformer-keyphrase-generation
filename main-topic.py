# -*- coding: utf-8 -*-
# @author: Shang
# @file: main-topic2.py
# @time: 2021-04-16

import tensorflow as tf
import texar.tf as tx
from texar.tf.modules import TransformerDecoder, BERTEncoder
from texar.tf.utils import transformer_utils
from bleu_tool import bleu_wrapper
from rouge import FilesRouge
from time import gmtime, strftime
from texar.tf.utils.shapes import shape_list
import config_model
import config_data

from utils import utils
from utils.data_utils import bos_token_id, eos_token_id, InputExample, convert_single_example, PredictProcessor
from utils.file_writer_utils import write_token_id_arrays_to_text_file

import numpy as np
import os
import sys
os.environ['CUDA_VISIBLE_DEVICES'] = '2'
config = tf.ConfigProto()
config.gpu_options.allow_growth=True
sess = tf.Session(config=config)
flags = tf.flags

flags.DEFINE_string("run_mode", "train_and_evaluate", "Either train_and_evaluate, test or predict.")

FLAGS = flags.FLAGS


model_dir = config_data.model_dir
topic_path = config_data.topic_dir


def readtxt(path):
    with open(path,'r',encoding='utf8') as f:
        data=f.read().replace('\n',' ').replace('[','').replace(']','').split()
        data=[float(i) for i in data]
    return data


def print_rouge_scores(scores):
    """Prints the rouge scores in a nice, human-readable format."""
    rouge_1 = scores['rouge-1']
    rouge_2 = scores['rouge-2']
    rouge_l = scores['rouge-l']

    print("┌─────────┬────────┬────────┬────────┐")
    print("│ Metric  │ Pre    │ Rec    │ F      │")
    print("├─────────┼────────┼────────┼────────┤")
    print("│ ROUGE-1 │ %.4f │ %.4f │ %.4f │" % (rouge_1['p'], rouge_1['r'], rouge_1['f']))
    print("│ ROUGE-2 │ %.4f │ %.4f │ %.4f │" % (rouge_2['p'], rouge_2['r'], rouge_2['f']))
    print("│ ROUGE-L │ %.4f │ %.4f │ %.4f │" % (rouge_l['p'], rouge_l['r'], rouge_l['f']))
    print("└─────────┴────────┴────────┴────────┘")


def get_data_iterator():
    train_dataset = tx.data.TFRecordData(hparams=config_data.train_hparam)
    eval_dataset = tx.data.TFRecordData(hparams=config_data.eval_hparam)
    test_dataset = tx.data.TFRecordData(hparams=config_data.test_hparam)

    iterator = tx.data.FeedableDataIterator({'train': train_dataset, 'eval': eval_dataset, 'test': test_dataset})

    return iterator


def main():

    print(config_data.tfrecord_data_dir)
    tokenizer = tx.data.BERTTokenizer(pretrained_model_name=config_model.bert['pretrained_model_name'])

    data_iterator = get_data_iterator()
    batch = data_iterator.get_next()
    # print('batch:',batch)

    src_input_ids = batch['src_input_ids']
    src_segment_ids = batch['src_segment_ids']
    tgt_input_ids = batch['tgt_input_ids']
    tgt_labels = batch['tgt_labels']
    # print('src_input_ids:',src_input_ids)
    # print('src_segment_ids:', src_segment_ids)

    is_target = tf.cast(tf.not_equal(tgt_labels, 0), tf.float32)

    batch_size = tf.shape(src_input_ids)[0]
    input_length = tf.reduce_sum(1 - tf.cast(tf.equal(src_input_ids, 0), tf.int32), axis=1)
    print('batch_size:', batch_size,'\n')

    beam_width = config_model.beam_width

    encoder = BERTEncoder(pretrained_model_name=config_model.bert['pretrained_model_name'])
    encoder_output, encoder_pooled_output = encoder(inputs=src_input_ids,
                                                    segment_ids=src_segment_ids,
                                                    sequence_length=input_length)
    print('encoder_output:', encoder_output,'\n')

    # Topic aware attention
    # beta:K * V
    # p:K * H
    # p_np = np.random.randint(0, 1, [30, 768])
    # beta = np.random.randint(0, 1, [50, 77768])
    beta = np.loadtxt(topic_path)
    beta = tf.convert_to_tensor(beta)
    beta_tensor = tf.cast(beta, tf.float32)

    fc1 = tf.keras.layers.Dense(768, input_shape=(shape_list(beta_tensor)[1],), activation=None)
    residual = fc1(beta_tensor)

    p = tf.nn.softmax(residual)
    p = tf.layers.dropout(p, 0.1, training=True)
    fc2 = tf.keras.layers.LayerNormalization(axis=-1)
    p_tensor = tf.add(residual, fc2(p))


    i = tf.get_variable("i", dtype=tf.int32, shape=[], initializer=tf.ones_initializer())
    print('i:',i,'\n')
    n = batch_size
    update_encoder_outputs = tf.get_variable("update_encoder_output", dtype=tf.float32, shape=[1,config_data.max_seq_length,768])


    # @tf.function
    def cond(i,n,p_tensor, encoder_output,update_encoder_outputs):
        return i<=n

    # @tf.function
    def body(i,n,p_tensor, encoder_output,update_encoder_outputs):
        # encoder_output[batch_num]: max_length * H
        # a=hp^T : [max_length, k]
        # topic_token_attention: max_length * K
        token_attention = tf.matmul(encoder_output[i-1], tf.transpose(p_tensor, (1, 0)))
        print(f"topic_token_attention: {token_attention}")

        # alpha: max_length * 1
        alpha = tf.nn.softmax(tf.reduce_mean(token_attention, axis=1, keep_dims=True), axis=0)
        # alpha = tf.transpose(alpha, (1, 0))
        print(f"alpha: {alpha}")

        one_diag = tf.diag(tf.ones(shape_list(alpha)[0]))
        one_diag_m = one_diag[:,1:]
        weight_matrix = tf.concat([alpha,one_diag_m],1)

        # update_encoder_outputs: batch_size * max_length * H
        update_encoder_output = [tf.matmul(weight_matrix, encoder_output[i-1])]
        update_encoder_outputs = tf.concat([update_encoder_outputs, update_encoder_output], axis=0)
        # print(f"update_encoder_outputs: {update_encoder_outputs}")
        i=i+1
        return i,n,p_tensor,encoder_output,update_encoder_outputs

    i, n, p_tensor, encoder_output,update_encoder_outputs = tf.while_loop(cond, body, [i, n, p_tensor, encoder_output,update_encoder_outputs],
                    shape_invariants=[i.get_shape(), n.get_shape(), p_tensor.get_shape(),encoder_output.get_shape(),tf.TensorShape([None,config_data.max_seq_length,768])])

    update_encoder_outputs = update_encoder_outputs[1:]
    print(f"update_encoder_outputs: {update_encoder_outputs}")

    vocab_size = config_model.vocab_size

    src_word_embedder = encoder.word_embedder
    pos_embedder = encoder.position_embedder

    tgt_embedding = tf.concat(
        [tf.zeros(shape=[1, src_word_embedder.dim]),
         src_word_embedder.embedding[1:, :]],
        axis=0)
    tgt_embedder = tx.modules.WordEmbedder(tgt_embedding)
    tgt_word_embeds = tgt_embedder(tgt_input_ids)
    tgt_word_embeds = tgt_word_embeds * config_model.hidden_dim ** 0.5

    tgt_seq_len = tf.ones([batch_size], tf.int32) * tf.shape(tgt_input_ids)[1]
    tgt_pos_embeds = pos_embedder(sequence_length=tgt_seq_len)

    tgt_input_embedding = tgt_word_embeds + tgt_pos_embeds

    _output_w = tf.transpose(tgt_embedder.embedding, (1, 0))
    # print(_output_w.shape)
    decoder = TransformerDecoder(vocab_size=vocab_size,
                                 output_layer=_output_w,
                                 hparams=config_model.decoder)

    # For training
    decoder_outputs = decoder(
        memory=update_encoder_outputs,
        memory_sequence_length=input_length,
        inputs=tgt_input_embedding,
        decoding_strategy='train_greedy',
        mode=tf.estimator.ModeKeys.TRAIN
    )
    # print(decoder_outputs.logits.shape)

    mle_loss = transformer_utils.smoothing_cross_entropy(
        decoder_outputs.logits, tgt_labels, vocab_size, config_model.loss_label_confidence)
    mle_loss = tf.reduce_sum(mle_loss * is_target) / tf.reduce_sum(is_target)


    # print(mle_loss.shape)
    global_step = tf.Variable(0, dtype=tf.int64, trainable=False)
    learning_rate = tf.placeholder(tf.float64, shape=(), name='lr')

    train_op = tx.core.get_train_op(
        mle_loss,
        learning_rate=learning_rate,
        global_step=global_step,
        hparams=config_model.opt)

    tf.summary.scalar('lr', learning_rate)
    tf.summary.scalar('mle_loss', mle_loss)
    summary_merged = tf.summary.merge_all()

    # For inference (beam-search)
    start_tokens = tf.fill([batch_size], bos_token_id)

    saver = tf.train.Saver(max_to_keep=5)
    best_results = {'score': 0, 'epoch': -1}

    def _embedding_fn(x, y):
        x_w_embed = tgt_embedder(x)
        y_p_embed = pos_embedder(y)
        return x_w_embed * config_model.hidden_dim ** 0.5 + y_p_embed

    predictions = decoder(
        memory=update_encoder_outputs,
        memory_sequence_length=input_length,
        beam_width=beam_width,
        start_tokens=start_tokens,
        end_token=eos_token_id,
        embedding=_embedding_fn,
        max_decoding_length=config_data.max_decoding_length,
        decoding_strategy='infer_greedy',
        mode=tf.estimator.ModeKeys.PREDICT)

    # Uses the best sample by beam search
    beam_search_ids = predictions['sample_id'][:, :, 0]

    def _train_epoch(sess, epoch, step, smry_writer):
        print('Start epoch %d' % epoch)
        data_iterator.restart_dataset(sess, 'train')

        fetches = {
            'train_op': train_op,
            'loss': mle_loss,
            'step': global_step,
            'smry': summary_merged
        }

        while True:
            try:
                feed_dict = {
                    data_iterator.handle: data_iterator.get_handle(sess, 'train'),
                    tx.global_mode(): tf.estimator.ModeKeys.TRAIN,
                    learning_rate: utils.get_lr(step, config_model)
                }

                fetches_ = sess.run(fetches, feed_dict)
                step, loss = fetches_['step'], fetches_['loss']


                # Display every display_steps
                display_steps = config_data.display_steps
                if display_steps > 0 and step % display_steps == 0:
                    print('[%s] step: %d, loss: %.4f' % (strftime("%Y-%m-%d %H:%M:%S", gmtime()), step, loss))
                    smry_writer.add_summary(fetches_['smry'], global_step=step)

                # Eval every eval_steps
                eval_steps = config_data.eval_steps
                if eval_steps > 0 and step % eval_steps == 0 and step > 0:
                    _eval_epoch(sess, epoch, 'eval')

            except tf.errors.OutOfRangeError:
                break

        return step

    def _eval_epoch(sess, epoch, mode):
        print('Starting %s' % mode)

        if mode is not 'eval' and not 'test':
            print("Unknown mode!")
            raise

        dataset_name = 'eval' if mode is 'eval' else 'test'

        data_iterator.restart_dataset(sess, dataset_name)
        references, hypotheses, inputs = [], [], []

        while True:
            try:
                feed_dict = {
                    data_iterator.handle: data_iterator.get_handle(sess, dataset_name),
                    tx.global_mode(): tf.estimator.ModeKeys.EVAL,
                }
                fetches = {
                    'beam_search_ids': beam_search_ids,
                    'tgt_labels': tgt_labels,
                    # src_input_ids is not necessary for calculating the metric, but allows us to write it to a file.
                    'src_input_ids': src_input_ids
                }
                fetches_ = sess.run(fetches, feed_dict=feed_dict)

                hypotheses.extend(h.tolist() for h in fetches_['beam_search_ids'])
                references.extend(r.tolist() for r in fetches_['tgt_labels'])
                inputs.extend(h.tolist() for h in fetches_['src_input_ids'])
                hypotheses = utils.list_strip_eos(hypotheses, eos_token_id)
                references = utils.list_strip_eos(references, eos_token_id)
            except tf.errors.OutOfRangeError:
                break

        def calculate_scores():
            hyp_fn, ref_fn = 'tmp.%s.src' % mode, 'tmp.%s.tgt' % mode
            write_token_id_arrays_to_text_file(hypotheses, os.path.join(model_dir, hyp_fn), tokenizer)
            write_token_id_arrays_to_text_file(references, os.path.join(model_dir, ref_fn), tokenizer)

            hyp_fn, ref_fn = os.path.join(model_dir, hyp_fn), os.path.join(model_dir, ref_fn)

            files_rouge = FilesRouge(hyp_fn, ref_fn)
            rouge_scores = files_rouge.get_scores(avg=True)

            bleu_score = bleu_wrapper(ref_fn, hyp_fn, case_sensitive=True)

            return rouge_scores, bleu_score

        if mode == 'eval':
            try:
                rouge_scores, bleu_score = calculate_scores()
            except ValueError:
                print("Failed to calculate rouge scores!")
                return

            print_rouge_scores(rouge_scores)
            print('epoch: %d, bleu_score %.4f' % (epoch, bleu_score))
            if bleu_score > best_results['score']:
                best_results['score'] = bleu_score
                best_results['epoch'] = epoch
                model_path = os.path.join(model_dir, 'best-model.ckpt')
                print('saving model to %s' % model_path)

                # Also save the best results in a text file for manual evaluation
                write_token_id_arrays_to_text_file(inputs, os.path.join(model_dir, 'eval-inputs.txt'), tokenizer)
                write_token_id_arrays_to_text_file(hypotheses, os.path.join(model_dir, 'eval-predictions.txt'),
                                                   tokenizer)
                write_token_id_arrays_to_text_file(references, os.path.join(model_dir, 'eval-targets.txt'), tokenizer)

                saver.save(sess, model_path)

            if epoch - best_results['epoch'] >5:
                print('early stop!')
                sys.exit()

        elif mode == 'test':
            rouge_scores, bleu_score = calculate_scores()

            print_rouge_scores(rouge_scores)
            print('bleu_score %.4f' % bleu_score)

            # Also save the results in a text file for manual evaluation
            write_token_id_arrays_to_text_file(inputs, os.path.join(model_dir, 'test-inputs.txt'), tokenizer)
            write_token_id_arrays_to_text_file(hypotheses, os.path.join(model_dir, 'test-predictions.txt'), tokenizer)
            write_token_id_arrays_to_text_file(references, os.path.join(model_dir, 'test-targets.txt'), tokenizer)

    def _predict(sess, examples: [InputExample]):
        hypotheses, inputs = [], []

        features = []
        for example in examples:
            feature = convert_single_example(ex_index=0, example=example, max_seq_length=config_data.max_seq_length,
                                             tokenizer=tokenizer)
            features.append(feature)

        for feature in features:
            feed_dict = {
                src_input_ids: [feature.src_input_ids],
                src_segment_ids: [feature.src_segment_ids],
                tx.global_mode(): tf.estimator.ModeKeys.PREDICT,
            }

            fetches = {
                'beam_search_ids': beam_search_ids,
                'src_input_ids': src_input_ids
            }

            fetches_ = sess.run(fetches, feed_dict=feed_dict)

            hypotheses.extend(h.tolist() for h in fetches_['beam_search_ids'])
            inputs.extend(h.tolist() for h in fetches_['src_input_ids'])
            hypotheses = utils.list_strip_eos(hypotheses, eos_token_id)

        write_token_id_arrays_to_text_file(inputs, os.path.join(model_dir, 'predict-inputs.txt'), tokenizer)
        write_token_id_arrays_to_text_file(hypotheses, os.path.join(model_dir, 'predict-predictions.txt'), tokenizer)

    # Run the graph
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        sess.run(tf.local_variables_initializer())
        sess.run(tf.tables_initializer())

        smry_writer = tf.summary.FileWriter(model_dir, graph=sess.graph)

        if FLAGS.run_mode == 'train_and_evaluate':
            print('Begin running with %s mode' % FLAGS.run_mode)

            if tf.train.latest_checkpoint(model_dir) is not None:
                print('Restore latest checkpoint in %s' % model_dir)
                saver.restore(sess, tf.train.latest_checkpoint(model_dir))

            step = 0
            for epoch in range(config_data.max_train_epoch):
                step = _train_epoch(sess, epoch, step, smry_writer)

        elif FLAGS.run_mode == 'test':
            print('Begin running with %s mode' % FLAGS.run_mode)

            print('Restore latest checkpoint in %s' % model_dir)
            saver.restore(sess, tf.train.latest_checkpoint(model_dir))

            _eval_epoch(sess, 0, mode='test')

        elif FLAGS.run_mode == 'predict':
            print('Begin running with %s mode' % FLAGS.run_mode)

            print('Restore latest checkpoint in %s' % model_dir)
            saver.restore(sess, tf.train.latest_checkpoint(model_dir))

            processor = PredictProcessor()

            _predict(sess=sess,
                     examples=processor.get_examples(data_dir='./data'))

        else:
            raise ValueError('Unknown mode: {}'.format(FLAGS.run_mode))


if __name__ == '__main__':
    main()
