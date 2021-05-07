# Taken from https://github.com/asyml/texar/blob/master/examples/bert/config_data.py and modified
tfrecord_data_dir = "data/stack"


if tfrecord_data_dir.split('/')[1]=='kp20k':
    max_seq_length = 300
    max_decoding_length = 50
    vocab_size = 30522
    bert = {
        'pretrained_model_name': 'bert-base-uncased'
    }
    model_dir = './model-topic/kp20k'
    topic_dir = './topic/kp20k/topic_word_distribution.txt'
elif tfrecord_data_dir.split('/')[1]=='stack':
    max_seq_length = 150
    max_decoding_length = 30
    vocab_size = 30522
    bert = {
        'pretrained_model_name': 'bert-base-uncased'
    }
    model_dir = './model-topic/stack2'
    topic_dir = './topic/stack/topic_word_distribution.txt'
else:
    max_seq_length = 100
    max_decoding_length = 10
    vocab_size = 21128
    bert = {
        'pretrained_model_name': 'bert-base-chinese'
    }
    model_dir = './model/weibo'
    # topic_dir = './topic/weibo/topic_word_distribution.txt'

# num_train_data = 21128
# print(max_seq_length)
# print(max_trg_length)



max_train_epoch = 60
display_steps = 100  # Print training loss every display_steps; -1 to disable
eval_steps = 200  # Eval on the dev set every eval_steps; -1 to disable
train_batch_size = 32
eval_batch_size = 32
test_batch_size = 32



def post_predict_opts(parser):
    parser.add_argument('-pred', type=str, required=True,
                        help="Path of the prediction file.")
    parser.add_argument('-src', type=str, required=True,
                        help="Path of the source text file.")
    parser.add_argument('-trg', type=str, required=True,
                        help="Path of the target text file.")

    parser.add_argument('-export_filtered_pred', action="store_true",
                        help="Export the filtered predictions to a file or not")
    parser.add_argument('-filtered_pred_path', type=str,
                        help="Path of the folder for storing the filtered prediction")
    parser.add_argument('-exp', type=str, default="kp20k",
                        help="Name of the experiment for logging.")
    parser.add_argument('-exp_path', type=str,
                        help="Path of experiment log/plot.")
    parser.add_argument('-disable_extra_one_word_filter', action="store_true",
                        help="If False, it will only keep the first one-word prediction")
    parser.add_argument('-disable_valid_filter', action="store_true",
                        help="If False, it will remove all the invalid predictions")
    parser.add_argument('-num_preds', type=int, default=50,
                        help='It will only consider the first num_preds keyphrases in each line of the prediction file')
    parser.add_argument('-debug', action="store_true", default=False,
                        help='Print out the metric at each step or not')
    parser.add_argument('-match_by_str', action="store_true", default=False,
                        help='If false, match the words at word level when checking present keyphrase. Else, match the words at string level.')
    parser.add_argument('-invalidate_unk', action="store_true", default=False,
                        help='Treat unk as invalid output')
    parser.add_argument('-target_separated', action="store_true", default=False,
                        help='The targets has already been separated into present keyphrases and absent keyphrases')
    parser.add_argument('-prediction_separated', action="store_true", default=False,
                        help='The predictions has already been separated into present keyphrases and absent keyphrases')
    parser.add_argument('-reverse_sorting', action="store_true", default=False,
                        help='Only effective in target separated.')


feature_original_types = {
    # Reading features from TFRecord data file.
    # E.g., Reading feature "src_input_ids" as dtype `tf.int64`;
    # "FixedLenFeature" indicates its length is fixed for all data instances;
    # and the sequence length is limited by `max_seq_length`.
    "src_input_ids": ["tf.int64", "FixedLenFeature", max_seq_length],
    "src_segment_ids": ["tf.int64", "FixedLenFeature", max_seq_length],
    "tgt_input_ids": ["tf.int64", "FixedLenFeature", max_decoding_length],
    "tgt_labels": ["tf.int64", "FixedLenFeature", max_decoding_length]
}

feature_convert_types = {
    # Converting feature dtype after reading. E.g.,
    # Converting the dtype of feature "src_input_ids" from `tf.int64` (as above)
    # to `tf.int32`
    "src_input_ids": "tf.int32",
    "src_segment_ids": "tf.int32",
    "tgt_input_ids": "tf.int32",
    "tgt_labels": "tf.int32"
}

train_hparam = {
    "allow_smaller_final_batch": False,
    "batch_size": train_batch_size,
    "dataset": {
        "data_name": "data",
        "feature_convert_types": feature_convert_types,
        "feature_original_types": feature_original_types,
        "files": "{}/train.tf_record".format(tfrecord_data_dir)
    },
    "shuffle": True,
    "shuffle_buffer_size": 100
}

eval_hparam = {
    "allow_smaller_final_batch": True,
    "batch_size": eval_batch_size,
    "dataset": {
        "data_name": "data",
        "feature_convert_types": feature_convert_types,
        "feature_original_types": feature_original_types,
        "files": "{}/eval.tf_record".format(tfrecord_data_dir)
    },
    "shuffle": False
}

test_hparam = {
    "allow_smaller_final_batch": True,
    "batch_size": test_batch_size,
    "dataset": {
        "data_name": "data",
        "feature_convert_types": feature_convert_types,
        "feature_original_types": feature_original_types,
        "files": "{}/test.tf_record".format(tfrecord_data_dir)
    },
    "shuffle": False
}