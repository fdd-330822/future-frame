import os
import sys
import torch

from torch.utils.data import DataLoader

from dataset import SequenceDataset
from loss import *
from models import PixelDiscriminator
from unet import UNet

from tqdm import tqdm

import cv2

from time import time

class args():
    eporchs = 10
    save_per_epoch = 5
    batch_size = 4
    pretrained = False
    save_model_dir = "./weight"
    save_logs_dir = "./logs"
    num_workers = 0
    resume = False
    g_lr_init = 0.0002
    d_lr_init = 0.00002
    channels = 3
    size = 256
    videos_dir = 'dataset/train'
    time_steps = 5

    gpu = 0

def check_cuda():
    if torch.cuda.is_available() and args.gpu is not None:
        return True
    else:
        return False

def weights_init_normal(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        torch.nn.init.normal_(m.weight.data, 0.0, 0.02)
    elif classname.find('BatchNorm2d') != -1:
        torch.nn.init.normal_(m.weight.data, 1.0, 0.02)
        torch.nn.init.constant_(m.bias.data, 0.0)

def train():

    use_cuda = check_cuda()

    generator = UNet(in_channels=args.channels * (args.time_steps - 1), out_channels=args.channels)
    discriminator = PixelDiscriminator(input_nc=args.channels)
    optimizer_G = torch.optim.Adam(generator.parameters(), lr=args.g_lr_init)
    optimizer_D = torch.optim.Adam(discriminator.parameters(), lr=args.d_lr_init)

    intensity_loss = IntensityLoss()
    gradient_loss = GradinetLoss(args.channels)
    adversarial_loss = GeneratorAdversarialLoss()
    discriminator_loss = DiscriminatorAdversarialLoss()

    if args.resume:
        generator.load_state_dict(torch.load(args.resume)['generator'])
        discriminator.load_state_dict(torch.load(args.resume)[discriminator])
        optimizer_G.load_state_dict(torch.load(args.resume)['optimizer_G'])
        optimizer_D.load_state_dict(torch.load(args.resume)['optimizer_D'])
        print(f'Pretrained models have been loaded.\n')
    else:
        generator.apply(weights_init_normal)
        discriminator.apply(weights_init_normal)
        print('Learning from scratch.')

    if use_cuda:
        torch.cuda.set_device(args.gpu)
        generator = generator.cuda()
        discriminator = discriminator.cuda()

    else:
        print('Using CPU, this will be slow')

    trainloader = DataLoader(dataset=SequenceDataset(channels=args.channels, size=args.size, videos_dir=args.videos_dir, time_steps=args.time_steps), batch_size=args.batch_size, shuffle=False, num_workers=args.num_workers)

    generator.train()
    discriminator.train()

    with torch.set_grad_enabled(True):
        for ep in range(args.eporchs):

            g_loss_sum = 0
            d_loss_sum = 0

            for i, clips in enumerate(trainloader):
                pbar = tqdm(clips)
                for j, frames in enumerate(pbar):

                    inputs = frames[:, 0:args.channels * (args.time_steps - 1), :, :]
                    last = frames[:, args.channels * (args.time_steps - 2):args.channels * (args.time_steps -1), :, :]
                    target = frames[:, args.channels * (args.time_steps -1):args.channels * args.time_steps, :, :]

                    if use_cuda:
                        inputs = inputs.cuda()
                        last = last.cuda()
                        target = target.cuda()

                    generated = generator(inputs)
                    d_t = discriminator(target)
                    d_g = discriminator(generated)

                    if use_cuda:
                        generated = generated.cuda()
                        target = target.cuda()
                        d_t = d_t.cuda()
                        d_g = d_g.cuda()

                    # print(generated.device)
                    # print(target.device)
                    int_loss = intensity_loss(generated, target)
                    grad_loss = gradient_loss(generated, target)
                    adv_loss = adversarial_loss(d_g)

                    g_loss = int_loss + grad_loss + 0.05*adv_loss

                    d_loss = discriminator_loss(d_t, d_g)

                    optimizer_D.zero_grad()
                    d_loss.backward(retain_graph=True)
                    optimizer_G.zero_grad()
                    g_loss.backward()
                    optimizer_D.step()
                    optimizer_G.step()

                    if use_cuda:
                        torch.cuda.synchronize()

                    d_loss_sum += d_loss.item()
                    g_loss_sum += g_loss.item()

                    if j == 0:
                        diff_map = torch.sum(torch.abs(generated - target)[0], 0)
                        diff_map -= diff_map.min()
                        diff_map /= diff_map.max()
                        diff_map *= 255
                        diff_map = diff_map.detach().cpu().numpy().astype('uint8')
                        heat_map = cv2.applyColorMap(diff_map, cv2.COLORMAP_JET)
                        cv2.imwrite(os.path.join(args.save_logs_dir, f'{ep}_{i}_{time()}.jpg'), heat_map)
                
                    pbar.set_postfix(_1_Epoch=f'{ep+1}/{args.eporchs}',
                                    _2_int_loss=f'{int_loss:.5f}',
                                    _3_grad_loss=f'{grad_loss:.5f}',
                                    _4_adv_loss=f'{adv_loss:.5f}',
                                    _5_gen_loss=f'{g_loss.item():.5f}',
                                    _6_dis_loss=f'{d_loss.item():.5f}'
                                    )

            g_loss_mean = g_loss_sum / (len(clips) * len(trainloader))
            d_loss_mean = d_loss_sum / (len(clips) * len(trainloader))
            print('G Loss: ', g_loss_mean)
            print('D Loss: ', d_loss_mean)

            if(ep + 1) % args.save_per_epoch == 0:
                model_dict = {'generator': generator.state_dict(), 'optimizer_G': optimizer_G.state_dict(),
                               'discriminator': discriminator.state_dict(), 'optimizer_D': optimizer_D.state_dict() }
                torch.save(model_dict, os.path.join(args.save_model_dir, f'ckpt_{ep + 1}_{g_loss_mean}.pth'))

if __name__ == "__main__":
    train()