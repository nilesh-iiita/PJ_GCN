from __future__ import division
from __future__ import print_function
from operator import itemgetter
import time
import os
import sys

import tensorflow as tf
import numpy as np
from sklearn import metrics

from decagon.deep.optimizer import DecagonOptimizer
from decagon.deep.model import DecagonModel
from decagon.deep.minibatch import EdgeMinibatchIterator
from decagon.utility import rank_metrics, preprocessing
from process_data import DecagonData
import pickle

# Train on CPU (hide GPU) due to memory constraints
os.environ['CUDA_VISIBLE_DEVICES'] = ""

# Train on GPU
# os.environ["CUDA_DEVICE_ORDER"] = 'PCI_BUS_ID'
# os.environ["CUDA_VISIBLE_DEVICES"] = '0'
# config = tf.ConfigProto()
# config.gpu_options.allow_growth = True

np.random.seed(0)

# load selected training
with open("./data_decagon/training_samples_500.pkl", "rb") as f:
    et = pickle.load(f)
et = [1, 2]
et += et
print("The training edge types are: ", et)
print("Total ", int(len(et)/2), " DD edge types will be trained...")


###########################################################
#
# Functions
#
###########################################################
def get_accuracy_scores(edges_pos, edges_neg, edge_type):
    feed_dict.update({placeholders['dropout']: 0})
    feed_dict.update({placeholders['batch_edge_type_idx']: minibatch.edge_type2idx[edge_type]})
    feed_dict.update({placeholders['batch_row_edge_type']: edge_type[0]})
    feed_dict.update({placeholders['batch_col_edge_type']: edge_type[1]})
    rec = sess.run(opt.predictions, feed_dict=feed_dict)

    def sigmoid(x):
        return 1. / (1 + np.exp(-x))

    # Predict on test set of edges
    preds = []
    actual = []
    predicted = []
    edge_ind = 0
    for u, v in edges_pos[edge_type[:2]][edge_type[2]]:
        score = sigmoid(rec[u, v])
        preds.append(score)
        assert decagon.adj_mats_orig[edge_type[:2]][edge_type[2]][u, v] == 1, 'Problem 1'

        actual.append(edge_ind)
        predicted.append((score, edge_ind))
        edge_ind += 1

    preds_neg = []
    for u, v in edges_neg[edge_type[:2]][edge_type[2]]:
        score = sigmoid(rec[u, v])
        preds_neg.append(score)
        assert decagon.adj_mats_orig[edge_type[:2]][edge_type[2]][u, v] == 0, 'Problem 0'

        predicted.append((score, edge_ind))
        edge_ind += 1

    preds_all = np.hstack([preds, preds_neg])
    preds_all = np.nan_to_num(preds_all)
    labels_all = np.hstack([np.ones(len(preds)), np.zeros(len(preds_neg))])
    predicted = list(zip(*sorted(predicted, reverse=True, key=itemgetter(0))))[1]

    roc_sc = metrics.roc_auc_score(labels_all, preds_all)
    aupr_sc = metrics.average_precision_score(labels_all, preds_all)
    apk_sc = rank_metrics.apk(actual, predicted, k=50)

    return roc_sc, aupr_sc, apk_sc


def get_cost(edges_pos, edges_neg, edge_type):
    feed_dict.update({placeholders['dropout']: 0})
    feed_dict.update({placeholders['batch_edge_type_idx']: minibatch.edge_type2idx[edge_type]})
    feed_dict.update({placeholders['batch_row_edge_type']: edge_type[0]})
    feed_dict.update({placeholders['batch_col_edge_type']: edge_type[1]})
    cost = sess.run(opt.cost, feed_dict=feed_dict)

    return cost


def construct_placeholders(edge_types):
    placeholders = {
        'batch': tf.placeholder(tf.int32, name='batch'),
        'batch_edge_type_idx': tf.placeholder(tf.int32, shape=(), name='batch_edge_type_idx'),
        'batch_row_edge_type': tf.placeholder(tf.int32, shape=(), name='batch_row_edge_type'),
        'batch_col_edge_type': tf.placeholder(tf.int32, shape=(), name='batch_col_edge_type'),
        'degrees': tf.placeholder(tf.int32),
        'dropout': tf.placeholder_with_default(0., shape=()),
    }
    placeholders.update({
        'adj_mats_%d,%d,%d' % (i, j, k): tf.sparse_placeholder(tf.float32)
        for i, j in edge_types for k in range(edge_types[i, j])})
    placeholders.update({
        'feat_%d' % i: tf.sparse_placeholder(tf.float32)
        for i, _ in edge_types})
    return placeholders


# logging
# print("All Print Info will be written to output.log")
# stdout_backup = sys.stdout
# log_file = open("./output.log", "w")
# sys.stdout = log_file

begin_time = time.time()

###########################################################
#
# Load and preprocess data
#
###########################################################
# NUM_EDGE = 1307
# generate training edge types
# et = [i for i in range(NUM_EDGE)] + [i for i in range(NUM_EDGE)]            # ordered edge types
# et = [130, 644, 668, 1113, 1246, 762, 130, 644, 668, 1113, 1246, 762,]       # top 6 best
# et = [475, 1052, 1285, 104, 669, 1145, 475, 1052, 1285, 104, 669, 1145]     # top 6 worst
# et = [130, 644, 668, 1113, 1246, 762, 475, 1052, 130, 644, 668, 1113, 1246, 762, 475, 1052]     # 6best + 2worst

decagon = DecagonData(et)
val_test_size = 0.1


###########################################################
#
# Settings and placeholders
#
###########################################################
flags = tf.app.flags
FLAGS = flags.FLAGS
flags.DEFINE_integer('neg_sample_size', 1, 'Negative sample size.')
flags.DEFINE_float('learning_rate', 0.001, 'Initial learning rate.')
flags.DEFINE_integer('epochs', 1, 'Number of epochs to train.')
flags.DEFINE_integer('hidden1', 64, 'Number of units in hidden layer 1.')
flags.DEFINE_integer('hidden2', 32, 'Number of units in hidden layer 2.')
flags.DEFINE_float('weight_decay', 0, 'Weight for L2 loss on embedding matrix.')
flags.DEFINE_float('dropout', 0.1, 'Dropout rate (1 - keep probability).')
flags.DEFINE_float('max_margin', 0.1, 'Max margin parameter in hinge loss')
flags.DEFINE_integer('batch_size', 512, 'minibatch size.')
flags.DEFINE_boolean('bias', True, 'Bias term.')

# Important -- Do not evaluate/print validation performance every iteration as it can take
# substantial amount of time
# PRINT_PROGRESS_EVERY = 150

print("Defining placeholders")
placeholders = construct_placeholders(decagon.edge_types)

###########################################################
#
# Create minibatch iterator, model and optimizer
#
###########################################################

print("Create minibatch iterator")
minibatch = EdgeMinibatchIterator(
    adj_mats=decagon.adj_mats_orig,
    feat=decagon.feat,
    edge_types=decagon.edge_types,
    et=et,
    batch_size=FLAGS.batch_size,
    val_test_size=val_test_size,
)



print("Mini-batch finished!\n")
print("Create model")
model = DecagonModel(
    placeholders=placeholders,
    num_feat=decagon.num_feat,
    nonzero_feat=decagon.num_nonzero_feat,
    edge_types=decagon.edge_types,
    decoders=decagon.edge_type2decoder,
    name='bw12',
    logging='logging'
)

print("Create optimizer")
with tf.name_scope('optimizer'):
    opt = DecagonOptimizer(
        embeddings=model.embeddings,
        latent_inters=model.latent_inters,
        latent_varies=model.latent_varies,
        degrees=decagon.degrees,
        edge_types=decagon.edge_types,
        edge_type2dim=decagon.edge_type2dim,
        placeholders=placeholders,
        batch_size=FLAGS.batch_size,
        margin=FLAGS.max_margin
    )

print("Initialize session")
saver = tf.train.Saver()
sess = tf.Session()
sess.run(tf.global_variables_initializer())
feed_dict = {}

print("Preparation Time Cost: %f" % (time.time()-begin_time))
begin_time = time.time()
###########################################################
#
# Train model
#
###########################################################

print("Train model")
for epoch in range(FLAGS.epochs):

    minibatch.shuffle()
    itr = 0
    val_cost = 0
    test_cost = 0
    while not minibatch.end():
        # Construct feed dictionary
        feed_dict = minibatch.next_minibatch_feed_dict(placeholders=placeholders)
        feed_dict = minibatch.update_feed_dict(
            feed_dict=feed_dict,
            dropout=FLAGS.dropout,
            placeholders=placeholders)


        # t = time.time()

        # Training step: run single weight update
        outs = sess.run([opt.opt_op, opt.cost, opt.batch_edge_type_idx], feed_dict=feed_dict)
        train_cost = outs[1]
        batch_edge_type = outs[2]

        # ##### validation loss for a iteration #####
        # if itr % PRINT_PROGRESS_EVERY == 0:
        #     print(itr)
        #     val_auc, val_auprc, val_apk = get_accuracy_scores(
        #         minibatch.val_edges, minibatch.val_edges_false,
        #         minibatch.idx2edge_type[minibatch.current_edge_type_idx])
        #
        #     print("Epoch:", "%04d" % (epoch + 1), "Iter:", "%04d" % (itr + 1), "Edge:", "%04d" % batch_edge_type,
        #           "train_loss=", "{:.5f}".format(train_cost),
        #           "val_roc=", "{:.5f}".format(val_auc), "val_auprc=", "{:.5f}".format(val_auprc),
        #           "val_apk=", "{:.5f}".format(val_apk), "time=", "{:.5f}".format(time.time() - t))

        # loss on validation set
        val_cost += get_cost(minibatch.val_edges, minibatch.val_edges_false,
                             minibatch.idx2edge_type[minibatch.current_edge_type_idx])

        # loss on test set
        test_cost += get_cost(minibatch.test_edges, val_test_size,
                              minibatch.idx2edge_type[minibatch.current_edge_type_idx])

        # update iteration
        itr += 1
        # if itr == 10:
        #     break

    # validation and test loss for each epoch
    print("Epoch: ", "%04d" % epoch,
          "Validation cost: ", "{:.5f}".format(val_cost),
          "Test cost: ", "{:.5f}".format(test_cost))

    if (epoch+1) % 10 == 0:
        # save model to path
        path = "./tmp/ep" + str(epoch+1) + "/model.ckpt"
        save_path = saver.save(sess, path)
        print("Model saved in path: %s" % save_path)

    # prediction score of some of the epoch
    # print("--------- test score for epoch ", epoch)
    # for et in range(decagon.num_edge_types):
    #     roc_score, auprc_score, apk_score = get_accuracy_scores(
    #         minibatch.test_edges, minibatch.test_edges_false, minibatch.idx2edge_type[et])
    #     print("Edge type=", "[%02d, %02d, %02d]" % minibatch.idx2edge_type[et])
    #     print("Edge type:", "%04d" % et, "Test AUROC score", "{:.5f}".format(roc_score))
    #     print("Edge type:", "%04d" % et, "Test AUPRC score", "{:.5f}".format(auprc_score))
    #     print("Edge type:", "%04d" % et, "Test AP@k score", "{:.5f}".format(apk_score))
    #     print()

print("Optimization finished!")

print("Total Opt Time Cost: %f" % (time.time() - begin_time))


# # save model to path
# save_path = saver.save(sess, "./tmp/model.ckpt")
# print("Model saved in path: %s" % save_path)


for et in range(decagon.num_edge_types):
    roc_score, auprc_score, apk_score = get_accuracy_scores(
        minibatch.test_edges, minibatch.test_edges_false, minibatch.idx2edge_type[et])
    print("Edge type=", "[%02d, %02d, %02d]" % minibatch.idx2edge_type[et])
    print("Edge type:", "%04d" % et, "Test AUROC score", "{:.5f}".format(roc_score))
    print("Edge type:", "%04d" % et, "Test AUPRC score", "{:.5f}".format(auprc_score))
    print("Edge type:", "%04d" % et, "Test AP@k score", "{:.5f}".format(apk_score))
    print()

# log_file.close()
# sys.stdout = stdout_backup

print("Finished!")


