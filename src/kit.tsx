import { createElement, type ComponentType, type ReactNode } from 'react';
import { getSdk } from '@ervisio/plugin-sdk';
function wrap<P>(name:string):ComponentType<P>{const C=(props:P)=>createElement(getSdk().ui[name],props as Record<string,unknown>);C.displayName=name;return C as ComponentType<P>}
export const Button=wrap<{variant?:'primary'|'secondary'|'ghost'|'danger'|'danger-solid';icon?:string;size?:'sm'|'md'|'lg';disabled?:boolean;loading?:boolean;onClick?():void;children?:ReactNode}>('Button');
export const Icon=wrap<{name:string;size?:number}>('Icon');
export const Badge=wrap<{tone?:'ok'|'warn'|'err'|'info'|'neutral';children:ReactNode}>('Badge');
export const Select=wrap<{label?:ReactNode;value:string;onChange(v:string):void;options:{label:string;value:string}[];disabled?:boolean}>('Select');
export const Checkbox=wrap<{checked:boolean;onChange(v:boolean):void;label?:ReactNode}>('Checkbox');
export const Tabs=wrap<{items:{id:string;label:string;icon?:string;count?:number}[];value:string;onChange(v:string):void}>('Tabs');
export const StatCard=wrap<{hue:'term';icon:string;label:ReactNode;value:ReactNode}>('StatCard');
export const Skeleton=wrap<{lines?:number}>('Skeleton');
export const ConfirmDialog=wrap<{open:boolean;onClose():void;onConfirm():void;title:ReactNode;description:ReactNode;confirmLabel:string;confirmText?:string;danger?:boolean;icon?:string}>('ConfirmDialog');
export const toast={ok:(title:string,detail?:string)=>getSdk().ui.toast.ok(title,detail),err:(title:string,detail?:string)=>getSdk().ui.toast.err(title,detail),info:(title:string,detail?:string)=>getSdk().ui.toast.info(title,detail)};
