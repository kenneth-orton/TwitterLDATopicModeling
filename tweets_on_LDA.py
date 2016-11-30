# -*- coding: utf-8 -*-
import numpy as np
import gensim
from gensim import utils, corpora, models
import json
import sys
import re
import os
import ast
import multiprocessing
from functools import partial

# http://stackoverflow.com/questions/15365046/python-removing-pos-tags-from-a-txt-file
def write_topn_words(user_topics_dir, lda_model):
    if not os.path.exists(user_topics_dir + 'topn_words.txt'):
        print('Writing topn words for LDA model')
        reg_ex = re.compile('(?<![\s/])/[^\s/]+(?![\S/])')

        with open(user_topics_dir + 'topn_words.txt', 'w') as outfile:
            for i in range(lda_model.num_topics):
                outfile.write('{}\n'.format('Topic #' + str(i + 1) + ': '))
                for word, prob in lda_model.show_topic(i, topn=20):
                    word = reg_ex.sub('', word)
                    outfile.write('\t{}\n'.format(word.encode('utf-8')))
                outfile.write('\n')	

def combine_vector_dictionaries(user_topics_dir, community_doc_vecs):
    try:
        with open(user_topics_dir + 'all_community_doc_vecs.json', 'r') as all_community_file:
            all_community_doc_vecs = json.load(all_community_file)
    except:
        all_community_doc_vecs = {}

    all_community_doc_vecs.update(community_doc_vecs)
    with open(user_topics_dir + 'all_community_doc_vecs.json', 'w') as all_community_doc_vecs_file:
        json.dump(all_community_doc_vecs, all_community_doc_vecs_file, sort_keys=True, indent=4)

def preprocess_text(tweetpath):
    with open(tweetpath, 'r') as infile:
        text = ' '.join(line.rstrip('\n') for line in infile)
    # remove emoji's and links from tweets
    # http://stackoverflow.com/questions/26568722/remove-unicode-emoji-using-re-in-python
    try:
        reg_ex = re.compile(u'([\U0001F300-\U0001F64F])|([\U0001F680-\U0001F6FF])|([\U00002600-\U000027BF])')
    except:
        reg_ex = re.compile(u'([\u2600-\u27BF])|([\uD83C][\uDF00-\uDFFF])|([\uD83D][\uDC00-\uDE4F])|([\uD83D][\uDE80-\uDEFF])')
    text = reg_ex.sub('', text)
    # http://stackoverflow.com/questions/11331982/how-to-remove-any-url-within-a-string-in-python
    text = re.sub(r'\w+:\/{2}[\d\w-]+(\.[\d\w-]+)*(?:(?:\/[^\s/]*))*', '', text)
    text = re.sub(r'[^\w]', ' ', text) # remove hashtag
    #return list(utils.simple_preprocess(text, deacc=True, min_len=2, max_len=15))
    return utils.lemmatize(text)

# prepare text document for later conversion to bag of words
def convert_to_doc(tweetpath):
    pool = multiprocessing.Pool(max(1, multiprocessing.cpu_count() - 1))
    jobs = []
    
    for chunk_start, chunk_size in chunkify(tweetpath):
        func = partial(process_wrapper, tweetpath)
        jobs.append(pool.apply_async(func, (chunk_start, chunk_size)))

    document = [job.get() for job in jobs]
    document = [item for sublist in document for item in sublist]

    pool.close()
    return document

def get_user_document_vectors(inputs, user_id):
    community_dir = inputs[0]
    tweets_dir = inputs[1]
    user_topics_dir = inputs[2]
    dictionary = inputs[3]
    lda_model = inputs[4]

    user_id = str(user_id).strip()
    if os.path.exists(tweets_dir + user_id):
        tweetpath = tweets_dir + user_id
    else:
        return

    try:
        with open(user_topics_dir + 'all_community_doc_vecs.json', 'r') as all_community_file:
            all_community_doc_vecs = json.load(all_community_file)
    except:
        all_community_doc_vecs = {}

    if not user_id in all_community_doc_vecs:
        document = preprocess_text(tweetpath)

        # create bag of words from input document
        doc_bow = dictionary.doc2bow(document)

        # queries the document against the LDA model and associates the data with probabalistic topics
        doc_lda = get_doc_topics(lda_model, doc_bow)
        dense_vec = gensim.matutils.sparse2full(doc_lda, lda_model.num_topics)
    
        # build dictionary of user document vectors <k, v>(user_id, vec)
        return (user_id, dense_vec.tolist())
    else:
        return (user_id, all_community_doc_vecs[user_id])
    
# http://stackoverflow.com/questions/17310933/document-topical-distribution-in-gensim-lda
def get_doc_topics(lda, bow):
    gamma, _ = lda.inference([bow])
    topic_dist = gamma[0] / sum(gamma[0])
    return [(topic_id, topic_value) for topic_id, topic_value in enumerate(topic_dist)]

def users_to_iter(community):
    for user in ast.literal_eval(community):
        yield user

# topology: topology file, output_dir: name of directory to create, dict_loc: dictionary, lda_loc: lda model,
# dir_prefix: prefix for subdirectories (ie community_1)

# python2.7 tweets_on_LDA.py communities user_topics_ex data/twitter/tweets.dict data/twitter/tweets_100_lem_5_pass.model community
def main(topology, tweets_dir, output_dir, dict_loc, lda_loc, dir_prefix):
    user_topics_dir = output_dir + '/'

    # create output directories
    if not os.path.exists(os.path.dirname(user_topics_dir)):
        os.makedirs(os.path.dirname(user_topics_dir), 0o755)

    # load wiki dictionary
    dictionary = corpora.Dictionary.load(dict_loc)

    # load trained wiki model from file
    lda_model = models.LdaModel.load(lda_loc)

    with open(topology, 'r') as topology_file:
        for i, community in enumerate(topology_file):
            community_dir = user_topics_dir + dir_prefix + '_' + str(i) + '/'
 
            if not os.path.exists(os.path.dirname(community_dir)):
                os.makedirs(os.path.dirname(community_dir), 0o755)
             
            print('Getting document vectors for community: ' + str(i))

            community_doc_vecs = {}
            pool = multiprocessing.Pool(max(1, multiprocessing.cpu_count() - 1))
            func = partial(get_user_document_vectors, (community_dir, tweets_dir, user_topics_dir, dictionary, lda_model))
            doc_vecs = pool.imap(func, users_to_iter(community))
            doc_vecs = [item for item in doc_vecs if item is not None]
            pool.terminate()
            community_doc_vecs = dict(doc_vecs)
 
            combine_vector_dictionaries(user_topics_dir, community_doc_vecs)
 
            # save each community document vector dictionary for later use
            with open(community_dir + '/community_doc_vecs.json', 'w') as community_doc_vecs_file:
                json.dump(community_doc_vecs, community_doc_vecs_file, sort_keys=True, indent=4)

	write_topn_words(user_topics_dir, lda_model)
            
if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]))
