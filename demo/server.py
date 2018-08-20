from flask import Flask, render_template, url_for
import os
import json
import datetime
from random import shuffle
from dataset import Dictionary, VQAFeatureDataset
from torch.utils.data import DataLoader
import dataset
import test
import base_model as base_model
from torch.utils.data import Dataset
from torch.autograd import Variable
import utils
import torch
import torch.nn as nn

# preload
dataroot = 'data/'
split = 'test2015'
question_path = os.path.join(
    dataroot, 'v2_OpenEnded_mscoco_%s_questions.json' % \
    (split + '2014' if 'test'!=split[:4] else split))
questions = sorted(json.load(open(question_path))['questions'],
    key=lambda x: x['question_id'])

# aggregate for image_id
images = dict()
for q in questions:
    set = images.get(q['image_id'], [])
    set = set + [q]
    images[q['image_id']] = set

imageids = list(images.keys())
shuffle(imageids)

def log(message):
    currenttime = datetime.datetime.today().strftime('%d/%b/%Y %H:%M:%S')
    print('[%s] %s' % (currenttime, message))

def tokenize(dictionary, question, max_length=14):
    """Tokenizes the questions.

    This will add q_token in each entry of the dataset.
    -1 represent nil, and should be treated as padding_idx in embedding
    """
    tokens = dictionary.tokenize(question, False)
    tokens = tokens[:max_length]
    if len(tokens) < max_length:
        # Note here we pad in front of the sentence
        padding = [dictionary.padding_idx] * (max_length - len(tokens))
        tokens = tokens + padding
    utils.assert_eq(len(tokens), max_length)
    return tokens

def _load_dataset(questions, img_id2val, label2ans):
    entries = []
    for question in questions:
        img_id = question['image_id']
        entries.append(dataset._create_entry(img_id2val[img_id], question, None))

    return entries

def postprocess(pred, dataloader):
    answers = []
    for i in range(pred.size(0)):
        answers = answers + [test.get_answer(pred[i], dataloader)]
    return answers

# load the vqa feature dataset
dictionary = Dictionary.load_from_file('data/dictionary.pkl')
log('loading VQAFeatureDataset ...')
eval_dset = VQAFeatureDataset('test2015', dictionary, dataroot='data', adaptive=True)
log('done.')
model = base_model.build_ban(eval_dset, 1280, op='c', gamma=8)

# load the pretrained model
model_path = 'saved_models/ban/model_epoch12.pth'
print('loading %s' % model_path)
model_data = torch.load(model_path)
model = nn.DataParallel(model).cuda()
model.load_state_dict(model_data.get('model_state', model_data))
model.train(False)

log('Server is prepared!')

app = Flask(__name__)

@app.route('/')
@app.route('/<imageid>')
def index(imageid=None):
    if imageid is None:
        shuffle(imageids)
        return(index(imageids[0]))
    try:
        imageid = int(imageid)
    except ValueError:
        shuffle(imageids)
        return(index(imageids[0]))
    impath = url_for('static', filename='data/%s/COCO_test2015_%012d.jpg' % (split, imageid))
    sample = images[imageid]

    # prepare dataloader
    eval_dset.entries = _load_dataset(sample, eval_dset.img_id2idx, eval_dset.label2ans)
    eval_dset.tokenize()
    eval_dset.tensorize(question_only=True)
    eval_loader = DataLoader(eval_dset, 10, shuffle=False, num_workers=1, collate_fn=utils.trim_collate)

    v, b, q, a = iter(eval_loader).next()
    v = Variable(v).cuda()
    b = Variable(b).cuda()
    q = Variable(q, volatile=True).cuda()
    pred, att = model(v, b, q, None)
    answers = postprocess(pred.data, eval_loader)
    for q, a in zip(sample, answers):
        q['answer'] = a

    return render_template('index.html', 
        imageid=imageid, 
        impath=impath,
        questions=sample)
