# -*- coding: utf-8 -*-
"""
Created on Feb 26, 2017
@author: Weiping Song
"""
import os
import tensorflow as tf
from tensorflow.python.ops import rnn_cell
import pandas as pd
import numpy as np

class GRU4Rec:

    def __init__(self, sess, args): # args is Class with attributes
        self.sess = sess
        self.is_training = args.is_training

        self.layers = args.layers
        self.rnn_size = args.rnn_size
        self.n_epochs = args.n_epochs
        self.batch_size = args.batch_size
        self.dropout_p_hidden = args.dropout_p_hidden
        self.learning_rate = args.learning_rate
        self.decay = args.decay
        self.decay_steps = args.decay_steps
        self.sigma = args.sigma
        self.init_as_normal = args.init_as_normal
        self.reset_after_session = args.reset_after_session
        self.session_key = args.session_key
        self.item_key = args.item_key
        self.time_key = args.time_key
        self.grad_cap = args.grad_cap
        self.n_items = args.n_items
        if args.hidden_act == 'tanh':
            self.hidden_act = self.tanh
        elif args.hidden_act == 'relu':
            self.hidden_act = self.relu
        else:
            raise NotImplementedError

        if args.loss == 'cross-entropy':
            if args.final_act == 'tanh':
                self.final_activation = self.softmaxth
            else:
                self.final_activation = self.softmax
            self.loss_function = self.cross_entropy
        elif args.loss == 'bpr':
            if args.final_act == 'linear':
                self.final_activation = self.linear
            elif args.final_act == 'relu':
                self.final_activation = self.relu
            else:
                self.final_activation = self.tanh
            self.loss_function = self.bpr
        elif args.loss == 'top1':
            if args.final_act == 'linear':
                self.final_activation = self.linear
            elif args.final_act == 'relu':
                self.final_activatin = self.relu
            else:
                self.final_activation = self.tanh
            self.loss_function = self.top1
        else:
            raise NotImplementedError

        self.checkpoint_dir = args.checkpoint_dir
        if not os.path.isdir(self.checkpoint_dir):
            raise Exception("[!] Checkpoint Dir not found")

        self.build_model()
        self.sess.run(tf.global_variables_initializer())
        self.saver = tf.train.Saver(tf.global_variables(), max_to_keep=10)

        if self.is_training:
            return

        # use self.predict_state to hold hidden states during prediction.
        self.predict_state = [np.zeros([self.batch_size, self.rnn_size], dtype=np.float32) for _ in range(self.layers)]
        ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            self.saver.restore(sess, '{}/gru-model-{}'.format(self.checkpoint_dir, args.test_model))

    ########################ACTIVATION FUNCTIONS#########################
    def linear(self, X):
        return X
    def tanh(self, X):
        return tf.nn.tanh(X)
    def softmax(self, X):
        return tf.nn.softmax(X)
    def softmaxth(self, X):
        return tf.nn.softmax(tf.tanh(X))
    def relu(self, X):
        return tf.nn.relu(X)
    def sigmoid(self, X):
        return tf.nn.sigmoid(X)

    ############################LOSS FUNCTIONS######################
    # FIXME(Huafeng Sheng): All these Activation Looks Wrong
    def cross_entropy(self, yhat):
        return tf.reduce_mean(-tf.log(tf.diag_part(yhat)+1e-24))
        # WTF is this cross-entropy???
    def bpr(self, yhat):
        yhatT = tf.transpose(yhat)
        return tf.reduce_mean(-tf.log(tf.nn.sigmoid(tf.diag_part(yhat)-yhatT)))
    def top1(self, yhat):
        yhatT = tf.transpose(yhat)
        term1 = tf.reduce_mean(tf.nn.sigmoid(-tf.diag_part(yhat)+yhatT)+tf.nn.sigmoid(yhatT**2), axis=0)
        term2 = tf.nn.sigmoid(tf.diag_part(yhat)**2) / self.batch_size
        return tf.reduce_mean(term1 - term2)

    def build_model(self):

        self.X = tf.placeholder(tf.int32, [self.batch_size], name='input')
        self.Y = tf.placeholder(tf.int32, [self.batch_size], name='output')
        self.state = [tf.placeholder(tf.float32, [self.batch_size, self.rnn_size], name='rnn_state') for _ in range(self.layers)]
        self.global_step = tf.Variable(0, name='global_step', trainable=False)

        with tf.variable_scope('gru_layer'):
            sigma = self.sigma if self.sigma != 0 else np.sqrt(6.0 / (self.n_items + self.rnn_size))
            if self.init_as_normal:
                initializer = tf.random_normal_initializer(mean=0, stddev=sigma)
            else:
                initializer = tf.random_uniform_initializer(minval=-sigma, maxval=sigma)

            # [FIXME]
            # 1. Fix embedding, W, b size.
            # 2. A more flexible MultiRNNCell
            # 3. Is this the only way to change memory of the cell?
            # here embedding layer dont have to be same as rnn_size. It can be arbitrary. There's no bug here. but it is not correct
            embedding = tf.get_variable('embedding', [self.n_items, self.rnn_size], initializer=initializer) # (n_items, dim_feat)
            # this guy is really doing softmax... not sampled one...
            softmax_W = tf.get_variable('softmax_w', [self.n_items, self.rnn_size], initializer=initializer) # n_items??... wrong!!! (n_y, rnn_size)
            softmax_b = tf.get_variable('softmax_b', [self.n_items], initializer=tf.constant_initializer(0.0)) # (n_y)

            '''To fully understand this part, you need to look through the tensorflow source code (check notes on evernote)'''
            # memory and output of GRU are same.
            cell = rnn_cell.GRUCell(self.rnn_size, activation=self.hidden_act)
            drop_cell = rnn_cell.DropoutWrapper(cell, output_keep_prob=self.dropout_p_hidden)
            # here, we can used different number of neurons for different RNN layer. Below way is not flexible
            stacked_cell = rnn_cell.MultiRNNCell([drop_cell] * self.layers) # here is __init__()

            inputs = tf.nn.embedding_lookup(embedding, self.X) # self.X = in_idx
            output, state = stacked_cell(inputs, tuple(self.state)) # here is call()
            # In this case, state is memory and output of GRU Cells.
            self.final_state = state

        if self.is_training:
            '''
            Use other examples of the minibatch as negative samples.
            by default, it is using sampled softmax.
            How is NCE Loss?
            '''
            #[HACK] is this efficient? will softmax_W all be calculated?
            # sampled_W = tf.nn.embedding_lookup(softmax_W, self.Y)
            # sampled_b = tf.nn.embedding_lookup(softmax_b, self.Y)
            # logits = tf.matmul(output, sampled_W, transpose_b=True) + sampled_b

            true_W = tf.nn.embedding_lookup(softmax_W, self.Y)
            true_b = tf.nn.embedding_lookup(softmax_b, self.Y)

            sampled_ids = self.Y[]

            logits = tf.matmul(output, sampled_W, transpose_b=True) + sampled_b
            # usually we don't need to calculate the activation by ourself... looks strange here. Maybe this is only for generalization purpose.

            # 这里重写:
            '''yhat不用定义吧？'''
            # self.yhat = self.final_activation(logits)
            # self.cost = self.loss_function(self.yhat)
        else:
            # This is doing softmax over all available items... looks expensive. Is this ok?
            logits = tf.matmul(output, softmax_W, transpose_b=True) + softmax_b
            self.yhat = self.final_activation(logits)

        # if not self.is_training:
        #     return

        self.lr = tf.maximum(1e-5,tf.train.exponential_decay(self.learning_rate, self.global_step, self.decay_steps, self.decay, staircase=True))

        '''
        Try different optimizers.
        '''
        #optimizer = tf.train.AdagradOptimizer(self.lr)
        optimizer = tf.train.AdamOptimizer(self.lr)
        #optimizer = tf.train.AdadeltaOptimizer(self.lr)
        #optimizer = tf.train.RMSPropOptimizer(self.lr)

        tvars = tf.trainable_variables()
        gvs = optimizer.compute_gradients(self.cost, tvars)
        # gradient clipping:
        if self.grad_cap > 0:
            capped_gvs = [(tf.clip_by_norm(grad, self.grad_cap), var) for grad, var in gvs]
        else:
            capped_gvs = gvs
        self.train_op = optimizer.apply_gradients(capped_gvs, global_step=self.global_step)
        # 没有写.minimizes 这样还对么。。。

    def init(self, data):
        data.sort_values([self.session_key, self.time_key], inplace=True)
        offset_sessions = np.zeros(data[self.session_key].nunique()+1, dtype=np.int32)
        offset_sessions[1:] = data.groupby(self.session_key).size().cumsum() # num_sessions=len(offset_sessions)-1
        return offset_sessions

    def fit(self, data):
        self.error_during_train = False
        itemids = data[self.item_key].unique()
        self.n_items = len(itemids)
        self.itemidmap = pd.Series(data=np.arange(self.n_items), index=itemids)
        # append an item_id column to data
        data = pd.merge(data, pd.DataFrame({self.item_key:itemids, 'ItemIdx':self.itemidmap[itemids].values}), on=self.item_key, how='inner')
        offset_sessions = self.init(data) # this is a list: denoting an accumulative session length
        # eg.: [0, 5, 20, 45, 78,...] (it is accumulative session len/index seperated by different sessions)
        print('fitting model...')
        for epoch in range(self.n_epochs):
            epoch_cost = []
            # state is Memory/Output of each GRU cell (a)
            state = [np.zeros([self.batch_size, self.rnn_size], dtype=np.float32) for _ in range(self.layers)]
            session_idx_arr = np.arange(len(offset_sessions)-1) # array([0,1,2,3,4,..., num_sessions-1])
            iters = np.arange(self.batch_size) # array([0,1,2,3,..., batch_size-1])
            maxiter = iters.max() # =batch_size-1
            # start and end is for indexing data. (start: start index of sessions in first batch; end: end index of sessions in first batch)
            '''
            offset: [0, 5, 20, 45, 78, ...]
            start:  offset[0, 1, 2, 3, 4, 5, ..., batch-1]
            end:    offset[1, 2, 3, 4, 5, 6, ..., batch]
            '''
            start = offset_sessions[session_idx_arr[iters]]  # start is just an array: start index of coming session
            end = offset_sessions[session_idx_arr[iters]+1]  # end is also an array: start index of next session
            finished = False
            while not finished:
                minlen = (end-start).min()  # session with minimal timestep (minimal timestep)
                out_idx = data.ItemIdx.values[start] # first Item indexes (input to RNN/Embedding) of first few sessions

                # This for loop would go through the all time steps of the GRU, limited by the shortest session. This is a truncated static_RNN
                for i in range(minlen-1): # because the first index has already taken above.
                    in_idx = out_idx
                    # next items in the same sessions
                    out_idx = data.ItemIdx.values[start+i+1] # this makes sure that all of these are in same session

                    # prepare inputs, targeted outputs and hidden states
                    fetches = [self.cost, self.final_state, self.global_step, self.lr, self.train_op]
                    feed_dict = {self.X: in_idx, self.Y: out_idx} # X: input to the RNN cell. Y: output of the RNN cell.
                    # Y is always the next item index of X. (Prediction)

                    # this expend the feed_dict with one more key and value pair
                    # self.state = [tf.placeholder(tf.float32, [self.batch_size, self.rnn_size], name='rnn_state') for _ in range(self.layers)]
                    for j in range(self.layers):
                        feed_dict[self.state[j]] = state[j]  # state: a with size (n_a, m)
                    # so feed_dict={self.X: , self.Y: , self.state: }
                    # self.state is a whole thing, [j] is just filling values

                    cost, state, step, lr, _ = self.sess.run(fetches, feed_dict)
                    epoch_cost.append(cost)
                    if np.isnan(cost):
                        print(str(epoch) + ':Nan error!')
                        self.error_during_train = True
                        return
                    if step == 1 or step % self.decay_steps == 0:
                        avgc = np.mean(epoch_cost)
                        print('Epoch {}\tStep {}\tlr: {:.6f}\tloss: {:.6f}'.format(epoch, step, lr, avgc))

                ####################################################################
                # Updata start
                start = start+minlen-1 # end index of trained item for each session
                # mask here
                mask = np.arange(len(iters))[(end-start)<=1] # index is the ones (in the batch) consumed all timesteps for the session
                # this is checking how many sessions it fully consumed
                for idx in mask:
                    maxiter += 1
                    # 这个是对的, 最初的batch就包含了num_batch个session.
                    if maxiter >= len(offset_sessions)-1: # len(offset_sessions)=num_sessions+1
                        finished = True
                        break
                    iters[idx] = maxiter
                    # inserting values for those early stoped short session
                    start[idx] = offset_sessions[session_idx_arr[maxiter]] # 先跑完当前session的先换到新的session
                    # Updata end
                    end[idx] = offset_sessions[session_idx_arr[maxiter]+1]

                # this step is key! rnn关键在于是否保留memory, 除非memory清0，不然还算是一个sequence.
                if len(mask) and self.reset_after_session:
                    for i in range(self.layers):
                        state[i][mask] = 0

            avgc = np.mean(epoch_cost)
            if np.isnan(avgc):
                print('Epoch {}: Nan error!'.format(epoch, avgc))
                self.error_during_train = True
                return
            self.saver.save(self.sess, '{}/gru-model'.format(self.checkpoint_dir), global_step=epoch)

    def predict_next_batch(self, session_ids, input_item_ids, itemidmap, batch=50):
        '''
        Gives predicton scores for a selected set of items. Can be used in batch mode to predict for multiple independent events (i.e. events of different sessions) at once and thus speed up evaluation.

        If the session ID at a given coordinate of the session_ids parameter remains the same during subsequent calls of the function, the corresponding hidden state of the network will be kept intact (i.e. that's how one can predict an item to a session).
        If it changes, the hidden state of the network is reset to zeros.

        Parameters
        --------
        session_ids : 1D array
            Contains the session IDs of the events of the batch. Its length must equal to the prediction batch size (batch param).
        input_item_ids : 1D array
            Contains the item IDs of the events of the batch. Every item ID must be must be in the training data of the network. Its length must equal to the prediction batch size (batch param).
        batch : int
            Prediction batch size.

        Returns
        --------
        out : pandas.DataFrame
            Prediction scores for selected items for every event of the batch.
            Columns: events of the batch; rows: items. Rows are indexed by the item IDs.

        '''
        if batch != self.batch_size:
            raise Exception('Predict batch size({}) must match train batch size({})'.format(batch, self.batch_size))
        if not self.predict:
            self.current_session = np.ones(batch) * -1
            self.predict = True

        session_change = np.arange(batch)[session_ids != self.current_session]
        if len(session_change) > 0: # change internal states with session changes
            for i in range(self.layers):
                self.predict_state[i][session_change] = 0.0
            self.current_session=session_ids.copy()

        in_idxs = itemidmap[input_item_ids]
        fetches = [self.yhat, self.final_state]
        feed_dict = {self.X: in_idxs}
        for i in range(self.layers):
            feed_dict[self.state[i]] = self.predict_state[i]
        preds, self.predict_state = self.sess.run(fetches, feed_dict)
        preds = np.asarray(preds).T
        return pd.DataFrame(data=preds, index=itemidmap.index)
